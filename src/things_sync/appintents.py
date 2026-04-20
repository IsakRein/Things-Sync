"""Parser for `Metadata.appintents/extract.actionsdata` bundles.

Every macOS app that declares App Intents ships a build-time catalog at
`<App>.app/Contents/Resources/Metadata.appintents/extract.actionsdata` — a
JSON file listing every intent, its parameters, return type, and the
entities/enums it references. Shortcuts.app reads this file to populate its
action picker.

This module exposes it as typed Python. Discovery only — execution would
require XPC to `BackgroundShortcutRunner` with the private
`com.apple.shortcuts.background-running` entitlement, which AMFI blocks for
non-Apple binaries.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


THINGS_BUNDLE_CANDIDATES = [
    Path("/Applications/Things.app"),
    Path("/Applications/Things3.app"),
    Path("/Applications/Setapp/Things3.app"),
    Path.home() / "Applications/Things.app",
    Path.home() / "Applications/Things3.app",
]


def find_things_bundle() -> Path | None:
    for p in THINGS_BUNDLE_CANDIDATES:
        if p.exists():
            return p
    return None


def metadata_path(bundle: Path) -> Path:
    return bundle / "Contents/Resources/Metadata.appintents/extract.actionsdata"


# ---------- models ----------


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class LocalizedString(_Base):
    key: str = ""
    alternatives: list[str] = Field(default_factory=list)

    def __str__(self) -> str:
        return self.key


class Parameter(_Base):
    name: str
    title: LocalizedString = Field(default_factory=LocalizedString)
    is_optional: bool = Field(alias="isOptional", default=True)
    is_input: bool = Field(alias="isInput", default=False)
    value_type: dict[str, Any] = Field(alias="valueType", default_factory=dict)

    @property
    def type_signature(self) -> str:
        return describe_value_type(self.value_type)


class ActionSummary(_Base):
    format_string: str = Field(alias="formatString", default="")
    parameter_identifiers: list[str] = Field(
        alias="parameterIdentifiers", default_factory=list
    )


class VisibilityMetadata(_Base):
    assistant_only: bool = Field(alias="assistantOnly", default=False)
    is_discoverable: bool = Field(alias="isDiscoverable", default=True)


class Intent(_Base):
    identifier: str
    fully_qualified_type_name: str = Field(alias="fullyQualifiedTypeName", default="")
    title: LocalizedString = Field(default_factory=LocalizedString)
    parameters: list[Parameter] = Field(default_factory=list)
    is_discoverable: bool = Field(alias="isDiscoverable", default=True)
    open_app_when_run: bool = Field(alias="openAppWhenRun", default=False)
    authentication_policy: int = Field(alias="authenticationPolicy", default=0)
    visibility_metadata: VisibilityMetadata = Field(
        alias="visibilityMetadata", default_factory=VisibilityMetadata
    )
    summary: ActionSummary = Field(default_factory=ActionSummary)

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "Intent":
        # Pull the summary string out of the nested actionConfiguration.
        summary_raw: dict[str, Any] = {}
        ac = raw.get("actionConfiguration", {})
        wrapper = ac.get("actionSummary", {}).get("wrapper", {})
        s = wrapper.get("summaryString")
        if isinstance(s, dict):
            summary_raw = s
        return cls.model_validate({**raw, "summary": summary_raw})


class Entity(_Base):
    type_name: str = Field(alias="typeName", default="")
    fully_qualified_type_name: str = Field(alias="fullyQualifiedTypeName", default="")
    display_type_name: LocalizedString = Field(
        alias="displayTypeName", default_factory=LocalizedString
    )
    default_query_identifier: str = Field(alias="defaultQueryIdentifier", default="")
    properties: list[dict[str, Any]] = Field(default_factory=list)


class EnumCase(_Base):
    name: str = ""
    title: LocalizedString = Field(default_factory=LocalizedString)


class Enum(_Base):
    type_name: str = Field(alias="typeName", default="")
    fully_qualified_type_name: str = Field(alias="fullyQualifiedTypeName", default="")
    cases: list[EnumCase] = Field(default_factory=list)


class Catalog(_Base):
    bundle_path: Path
    generator: dict[str, Any] = Field(default_factory=dict)
    version: str = ""
    intents: dict[str, Intent] = Field(default_factory=dict)
    entities: dict[str, Entity] = Field(default_factory=dict)
    enums: dict[str, Enum] = Field(default_factory=dict)

    def discoverable(self) -> list[Intent]:
        return [
            i for i in self.intents.values()
            if i.is_discoverable and not i.visibility_metadata.assistant_only
        ]


# ---------- value-type flattening ----------


def describe_value_type(vt: dict[str, Any]) -> str:
    """Flatten the nested valueType tagged-union into a signature string."""
    if not isinstance(vt, dict) or not vt:
        return "?"
    tag, body = next(iter(vt.items()))
    wrapper = body.get("wrapper", {}) if isinstance(body, dict) else {}

    if tag == "entity":
        return f"Entity({wrapper.get('typeName', '?')})"
    if tag == "enum":
        return f"Enum({wrapper.get('typeName', '?')})"
    if tag == "array":
        element = wrapper.get("elementType") or wrapper.get("valueType") or {}
        return f"[{describe_value_type(element)}]"
    if tag == "primitive":
        return wrapper.get("typeName") or wrapper.get("kind") or "Primitive"
    if tag == "file":
        return "File"
    if tag == "measurement":
        return f"Measurement({wrapper.get('unit', '?')})"
    # Fallback: preserve the tag so we notice unseen shapes.
    return tag.capitalize()


# ---------- loading ----------


def load(bundle: Path) -> Catalog:
    path = metadata_path(bundle)
    if not path.exists():
        raise FileNotFoundError(
            f"No App Intents metadata at {path}. "
            f"Either the app predates App Intents, or it wasn't built with them."
        )
    raw = json.loads(path.read_text())

    intents = {
        ident: Intent.from_raw(a)
        for ident, a in (raw.get("actions") or {}).items()
    }
    entities = {
        name: Entity.model_validate({**e, "typeName": name})
        for name, e in (raw.get("entities") or {}).items()
    }
    enums = {
        name: Enum.model_validate({**e, "typeName": name})
        for name, e in (raw.get("enums") or {}).items()
    }
    return Catalog(
        bundle_path=bundle,
        generator=raw.get("generator") or {},
        version=str(raw.get("version", "")),
        intents=intents,
        entities=entities,
        enums=enums,
    )
