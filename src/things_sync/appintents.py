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


def metadata_paths(bundle: Path) -> list[Path]:
    """All `extract.actionsdata` files in a bundle.

    Newer apps (iOS 18 / macOS 15+) use AppIntentsPackage — the top-level
    Metadata.appintents is empty and delegates to packages inside embedded
    frameworks (`Contents/Frameworks/*.framework/Versions/*/Resources/...`)
    and extensions (`Contents/PlugIns/*.appex/Contents/Resources/...`).
    """
    return sorted(bundle.glob("**/Metadata.appintents/extract.actionsdata"))


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
    package: str = ""  # which embedded framework/extension it came from

    @classmethod
    def from_raw(cls, raw: dict[str, Any], *, package: str = "") -> "Intent":
        summary_raw: dict[str, Any] = {}
        ac = raw.get("actionConfiguration", {})
        wrapper = ac.get("actionSummary", {}).get("wrapper", {})
        s = wrapper.get("summaryString")
        if isinstance(s, dict):
            summary_raw = s
        return cls.model_validate({**raw, "summary": summary_raw, "package": package})


class Entity(_Base):
    type_name: str = Field(alias="typeName", default="")
    fully_qualified_type_name: str = Field(alias="fullyQualifiedTypeName", default="")
    display_type_name: LocalizedString = Field(
        alias="displayTypeName", default_factory=LocalizedString
    )
    default_query_identifier: str = Field(alias="defaultQueryIdentifier", default="")
    properties: list[dict[str, Any]] = Field(default_factory=list)


class EnumCase(_Base):
    identifier: str = ""
    title: LocalizedString = Field(default_factory=LocalizedString)

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "EnumCase":
        title = raw.get("displayRepresentation", {}).get("title") or {}
        return cls(identifier=raw.get("identifier", ""), title=LocalizedString.model_validate(title))


class Enum(_Base):
    identifier: str = ""
    fully_qualified_type_name: str = Field(alias="fullyQualifiedTypeName", default="")
    display_type_name: LocalizedString = Field(
        alias="displayTypeName", default_factory=LocalizedString
    )
    cases: list[EnumCase] = Field(default_factory=list)

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "Enum":
        return cls(
            identifier=raw.get("identifier", ""),
            fullyQualifiedTypeName=raw.get("fullyQualifiedTypeName", ""),
            displayTypeName=LocalizedString.model_validate(raw.get("displayTypeName") or {}),
            cases=[EnumCase.from_raw(c) for c in raw.get("cases", [])],
        )


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


_PRIMITIVE_NAMES = {
    0: "String",
    1: "Bool",
    8: "DateTime",
    9: "Date",
    11: "URL",
}


def _primitive_kind(tid: Any) -> str | None:
    if tid is None:
        return None
    name = _PRIMITIVE_NAMES.get(int(tid)) if isinstance(tid, (int, str)) and str(tid).lstrip("-").isdigit() else None
    return name or f"Primitive({tid})"


def describe_value_type(vt: dict[str, Any]) -> str:
    """Flatten the nested valueType tagged-union into a signature string."""
    if not isinstance(vt, dict) or not vt:
        return "?"
    tag, body = next(iter(vt.items()))
    wrapper = body.get("wrapper", {}) if isinstance(body, dict) else {}

    if tag == "entity":
        return f"Entity({wrapper.get('typeName', '?')})"
    if tag in ("enum", "linkEnumeration"):
        return f"Enum({wrapper.get('identifier') or wrapper.get('typeName', '?')})"
    if tag == "array":
        element = (
            wrapper.get("memberValueType")
            or wrapper.get("elementType")
            or wrapper.get("valueType")
            or {}
        )
        return f"[{describe_value_type(element)}]"
    if tag == "primitive":
        return (
            wrapper.get("typeName")
            or wrapper.get("kind")
            or _primitive_kind(wrapper.get("typeIdentifier"))
            or "Primitive"
        )
    if tag == "file":
        return "File"
    if tag == "measurement":
        return f"Measurement({wrapper.get('unit', '?')})"
    # Fallback: preserve the tag so we notice unseen shapes.
    return tag.capitalize()


# ---------- loading ----------


def _package_name(path: Path, bundle: Path) -> str:
    """Derive a short package label from a nested metadata path.

    e.g. .../Frameworks/ThingsCommon.framework/... → "ThingsCommon"
         .../PlugIns/ThingsWidgetExtension.appex/... → "ThingsWidgetExtension"
         bundle top-level → "<app>"
    """
    rel = path.relative_to(bundle).parts
    for part in rel:
        if part.endswith(".framework"):
            return part.removesuffix(".framework")
        if part.endswith(".appex"):
            return part.removesuffix(".appex")
    return bundle.stem


def load(bundle: Path) -> Catalog:
    paths = metadata_paths(bundle)
    if not paths:
        raise FileNotFoundError(
            f"No App Intents metadata under {bundle}. "
            f"Either the app predates App Intents, or it wasn't built with them."
        )

    intents: dict[str, Intent] = {}
    entities: dict[str, Entity] = {}
    enums: dict[str, Enum] = {}
    generator: dict[str, Any] = {}
    version = ""

    for p in paths:
        raw = json.loads(p.read_text())
        pkg = _package_name(p, bundle)
        for ident, a in (raw.get("actions") or {}).items():
            intents[ident] = Intent.from_raw(a, package=pkg)
        # `entities` in the schema is a dict keyed by name.
        ent_raw = raw.get("entities") or {}
        for name, e in ent_raw.items():
            entities[name] = Entity.model_validate({**e, "typeName": name})
        # `enums` is a LIST of dicts in Things' schema (was dict in Excel's —
        # Apple's tooling emits both shapes depending on SDK version).
        enum_raw = raw.get("enums") or []
        if isinstance(enum_raw, dict):
            enum_raw = list(enum_raw.values())
        for e in enum_raw:
            en = Enum.from_raw(e)
            if en.identifier:
                enums[en.identifier] = en
        if not generator:
            generator = raw.get("generator") or {}
        if not version:
            version = str(raw.get("version", ""))

    return Catalog(
        bundle_path=bundle,
        generator=generator,
        version=version,
        intents=intents,
        entities=entities,
        enums=enums,
    )
