"""Direct access to Things Cloud (cloud.culturedcode.com).

This is an undocumented, reverse-engineered protocol. Schema can shift at any
Things release; if a commit returns 4xx/5xx, bump the `SCHEMA` constant and
re-check the field aliases below.

Endpoints (base = https://cloud.culturedcode.com):
    GET  /version/1/account/<email>                     — login, returns history-key
    POST /api/account/login/getT3SharedSession          — start a session, returns headIndex
    GET  /version/1/history/<history-key>/items?start-index=N
                                                        — pull changes since N
    POST /version/1/history/<history-key>/commit?ancestor-index=N&_cnt=1
                                                        — push a single commit body

Protocol shape (items / commit body, both directions):
    { "<short-uuid>": { "t": <0|1>, "e": "Task6"|"ChecklistItem3", "p": {...} } }
    where t=0 means NEW (full payload) and t=1 means EDIT (sparse delta).

All the `p` keys are 2-3 letter aliases; see `TodoPayload` below.
"""

from __future__ import annotations

import base64
import json
import os
import threading
from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import quote

import httpx
import shortuuid

SCHEMA = 301
BASE = "https://cloud.culturedcode.com"
API_BASE = f"{BASE}/version/1"
SERVICES_BASE = "https://services.culturedcode.com/version/1"

APP_ID = os.environ.get("THINGS_APP_ID", "com.culturedcode.ThingsMac")
USER_AGENT = os.environ.get(
    "THINGS_USER_AGENT",
    "ThingsMac/3.22.2 (Macintosh; Intel Mac OS X 14.4; en_US)",
)

STATE_DIR = Path(os.environ.get("THINGS_STATE_DIR") or Path.home() / ".cache" / "things-sync")
STATE_FILE = STATE_DIR / "state.json"

SHORT_UUID_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_shortuuid = shortuuid.ShortUUID(alphabet=SHORT_UUID_ALPHABET)


def new_uuid() -> str:
    return _shortuuid.random(length=22)


# ---------- time codecs ----------

def _now_ts() -> float:
    return datetime.now(tz=timezone.utc).timestamp()


def _today_midnight_utc_ts() -> int:
    return int(datetime.combine(Date.today(), time.min, tzinfo=timezone.utc).timestamp())


def _parse_when(when: str | None) -> int | None:
    """Accepts: today | tomorrow | YYYY-MM-DD. Returns a Unix int at UTC midnight."""
    if not when:
        return None
    w = when.strip().lower()
    if w == "today":
        d = Date.today()
    elif w == "tomorrow":
        from datetime import timedelta
        d = Date.today() + timedelta(days=1)
    else:
        d = Date.fromisoformat(when)
    return int(datetime.combine(d, time.min, tzinfo=timezone.utc).timestamp())


# ---------- auth ----------


@dataclass
class Credentials:
    email: str
    password: str

    @classmethod
    def from_env(cls) -> "Credentials":
        email = os.environ.get("THINGS_EMAIL")
        password = os.environ.get("THINGS_PASSWORD")
        if not email or not password:
            raise RuntimeError(
                "Set THINGS_EMAIL and THINGS_PASSWORD in the environment."
            )
        return cls(email=email, password=password)

    def as_b64son(self) -> str:
        payload = json.dumps({"ep": {"e": self.email, "p": self.password}}).encode("utf-8")
        return base64.b64encode(payload).decode("utf-8")


@dataclass
class AccountInfo:
    email: str
    history_key: str
    status: str
    sla_version_accepted: str = ""
    maildrop_email: str = ""

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "AccountInfo":
        return cls(
            email=data["email"],
            history_key=data["history-key"],
            status=data.get("status", ""),
            sla_version_accepted=str(data.get("SLA-version-accepted", "")),
            maildrop_email=data.get("maildrop-email", ""),
        )


@dataclass
class Account:
    credentials: Credentials
    info: AccountInfo

    @classmethod
    def login(cls, credentials: Credentials, *, timeout: float = 15.0) -> "Account":
        pw = quote(credentials.password, safe="'")
        url = f"{API_BASE}/account/{credentials.email}"
        r = httpx.get(url, headers={"Authorization": f"Password {pw}"}, timeout=timeout)
        if r.status_code == 401:
            raise CloudAuthError("Invalid Things Cloud credentials")
        r.raise_for_status()
        return cls(credentials=credentials, info=AccountInfo.from_json(r.json()))


class CloudAuthError(RuntimeError):
    pass


class CloudError(RuntimeError):
    pass


# ---------- payload ----------

# Status / destination / type enums match both local SQLite and cloud wire format.
STATUS_TODO = 0
STATUS_CANCELLED = 2
STATUS_COMPLETE = 3

DEST_INBOX = 0
DEST_ANYTIME = 1
DEST_SOMEDAY = 2

# `sb` field (show-block) — controls which day-bucket a to-do appears in.
SB_NONE = 0
SB_EVENING = 1
DEST_SOMEDAY = 2

TYPE_TASK = 0
TYPE_PROJECT = 1
TYPE_HEADING = 2

UPDATE_NEW = 0
UPDATE_EDIT = 1


def default_note(value: str = "") -> dict[str, Any]:
    return {"_t": "tx", "ch": 0, "v": value, "t": 1}


def default_xx() -> dict[str, Any]:
    return {"_t": "oo", "sn": {}}


def build_new_payload(
    *,
    title: str,
    notes: str = "",
    project_uuid: str | None = None,
    area_uuid: str | None = None,
    when_ts: int | None = None,
    deadline_ts: int | None = None,
    destination: int | None = None,
    is_project: bool = False,
    now: float | None = None,
) -> dict[str, Any]:
    """Build a full Task6 payload suitable for a NEW commit."""
    now = now if now is not None else _now_ts()
    if destination is None:
        # Things.app always sets st=DEST_ANYTIME for projects, regardless of
        # whether an area is set. Using DEST_INBOX on project CREATE is a
        # bug — captured via mitmproxy 2026-04-20. For to-dos the old
        # heuristic (inbox unless area/project/when is set) still matches.
        if is_project:
            destination = DEST_ANYTIME
        elif project_uuid or area_uuid or when_ts:
            destination = DEST_ANYTIME
        else:
            destination = DEST_INBOX

    payload: dict[str, Any] = {
        "ix": 0,
        "tt": title,
        "ss": STATUS_TODO,
        "st": destination,
        "cd": now,
        "md": now,
        "sr": when_ts,
        "tir": when_ts,
        "sp": None,
        "dd": deadline_ts,
        "tr": False,
        # `icp` (appears to be a sub-flag, not "is_project") must be False even
        # for projects. Things.app marks project-ness via `tp: TYPE_PROJECT`
        # alone. Setting icp:True on a project-create caused Things.app to
        # crash during sync-pull of the resulting history item (observed 2026-04-20).
        "icp": False,
        "pr": [project_uuid] if project_uuid else [],
        "ar": [area_uuid] if area_uuid else [],
        "sb": 0,
        "tg": [],
        "tp": TYPE_PROJECT if is_project else TYPE_TASK,
        "dds": None,
        "rt": [],
        "rmd": None,
        "dl": [],
        "do": 0,
        "lai": None,
        "agr": [],
        "lt": False,
        "icc": 0,
        "ti": 0,
        "ato": None,
        "icsd": None,
        "rp": None,
        "acrd": None,
        "rr": None,
        "nt": default_note(notes),
        "xx": default_xx(),
    }
    return payload


def build_edit_payload(fields: dict[str, Any]) -> dict[str, Any]:
    """Build a sparse delta body — only keys present are committed."""
    delta = {k: v for k, v in fields.items() if v is not None}
    delta["md"] = _now_ts()
    return delta


# ---------- client ----------


@dataclass
class CloudState:
    """Persisted locally so subsequent pulls are incremental.

    `instance_id` identifies this client to the server — must be unique per
    install so the server's fan-out push doesn't treat our commits as already
    delivered to the user's real Mac/iOS Things apps.
    """

    history_key: str = ""
    head_index: int = 0
    instance_id: str = ""

    @classmethod
    def load(cls) -> "CloudState":
        if STATE_FILE.exists():
            try:
                data = json.loads(STATE_FILE.read_text())
                return cls(
                    history_key=data.get("history_key", ""),
                    head_index=int(data.get("head_index", 0)),
                    instance_id=data.get("instance_id", ""),
                )
            except (OSError, ValueError, json.JSONDecodeError):
                pass
        return cls()

    def save(self) -> None:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(
                {
                    "history_key": self.history_key,
                    "head_index": self.head_index,
                    "instance_id": self.instance_id,
                }
            )
        )
        tmp.replace(STATE_FILE)


class ThingsCloud:
    """Thin client for the Things Cloud history protocol."""

    _lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(self, account: Account, *, timeout: float = 20.0) -> None:
        self.account = account
        self.state = CloudState.load()
        # If account changed (different history key), wipe cached head.
        if self.state.history_key and self.state.history_key != account.info.history_key:
            self.state = CloudState(history_key=account.info.history_key)
        if not self.state.history_key:
            self.state.history_key = account.info.history_key
        if not self.state.instance_id:
            # Distinct from any real Things.app install. Persisted so the
            # server's last-seen-per-instance tracking stays consistent.
            self.state.instance_id = new_uuid()
            self.state.save()

        self._client = httpx.Client(
            base_url=f"{API_BASE}/history/{account.info.history_key}",
            timeout=timeout,
            headers={
                "Accept": "application/json",
                "Accept-Charset": "UTF-8",
                "Accept-Language": "en-gb",
                "User-Agent": USER_AGENT,
                "Schema": str(SCHEMA),
                "Content-Type": "application/json; charset=UTF-8",
                "App-Id": APP_ID,
                "App-Instance-Id": self.state.instance_id,
                "Push-Priority": "5",
            },
        )

        # Lazily bootstrap head_index by asking the server what it is.
        if self.state.head_index == 0:
            self.refresh_head()

    def refresh_head(self) -> int:
        """Ask the server for its current head index. Used to bootstrap
        ancestor-index on commits. The response includes the full history from
        start_index onward, so keep a cached value after the first run."""
        data = self.fetch(start_index=0)
        head = int(data["current-item-index"])
        self.state.head_index = head
        self.state.save()
        return head

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "ThingsCloud":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    @classmethod
    def from_env(cls) -> "ThingsCloud":
        return cls(Account.login(Credentials.from_env()))

    # ---- server ops ----

    def fetch(self, start_index: int | None = None) -> dict[str, Any]:
        """GET /items?start-index=N. Returns raw decoded JSON."""
        idx = self.state.head_index if start_index is None else start_index
        r = self._client.get("/items", params={"start-index": str(idx)})
        if r.status_code >= 400:
            raise CloudError(f"Fetch failed [{r.status_code}]: {r.text[:300]}")
        return r.json()

    def commit(self, item_uuid: str, body: dict[str, Any], *, _retry: int = 1) -> int:
        """POST /commit. `body` is the `{t, e, p}` wrapper for one item.

        On a stale ancestor-index (409/410/412) we refresh and retry once —
        another device may have pushed between our last pull and this write.
        """
        with self._lock:
            params = {"ancestor-index": str(self.state.head_index), "_cnt": "1"}
            payload = {item_uuid: body}
            r = self._client.post("/commit", params=params, json=payload)
            if r.status_code in (409, 410, 412) and _retry > 0:
                self.refresh_head()
                return self.commit(item_uuid, body, _retry=_retry - 1)
            if r.status_code >= 400:
                raise CloudError(f"Commit failed [{r.status_code}]: {r.text[:300]}")
            data = r.json()
            new_head = int(data["server-head-index"])
            self.state.head_index = new_head
            self.state.save()
            return new_head

    # ---- high-level helpers ----

    def add_task(
        self,
        title: str,
        *,
        notes: str = "",
        when: str | None = None,
        deadline: str | None = None,
        project_uuid: str | None = None,
        area_uuid: str | None = None,
    ) -> str:
        uuid = new_uuid()
        body = {
            "t": UPDATE_NEW,
            "e": "Task6",
            "p": build_new_payload(
                title=title,
                notes=notes,
                project_uuid=project_uuid,
                area_uuid=area_uuid,
                when_ts=_parse_when(when),
                deadline_ts=_parse_when(deadline),
            ),
        }
        self.commit(uuid, body)
        return uuid

    def add_project(
        self,
        title: str,
        *,
        notes: str = "",
        area_uuid: str | None = None,
        deadline: str | None = None,
    ) -> str:
        uuid = new_uuid()
        body = {
            "t": UPDATE_NEW,
            "e": "Task6",
            "p": build_new_payload(
                title=title,
                notes=notes,
                area_uuid=area_uuid,
                deadline_ts=_parse_when(deadline),
                is_project=True,
                destination=DEST_ANYTIME,
            ),
        }
        self.commit(uuid, body)
        return uuid

    def add_recurring_todo(
        self,
        title: str,
        *,
        unit: str,                         # "week" | "month"
        frequency: int = 1,                # every N <unit>s
        mode: str = "schedule",            # "schedule" | "completion"
        anchor_date: Date | None = None,   # first/reference occurrence
        project_uuid: str | None = None,
        area_uuid: str | None = None,
        notes: str = "",
    ) -> tuple[str, str]:
        """**EXPERIMENTAL / BROKEN** — Creates a Things recurring to-do.

        The rr payload is close to Things.app's but subtly wrong — `tir`,
        `icsd`, field order, and some flag values differ in ways that
        crash Things.app during history replay. **Not wired into atlas
        push.** Kept as reference for future reverse-engineering.

        Recommended path for atlas today: keep `@repeat` in Tasks.md as
        source of truth, generate next instance locally (`atlas tasks
        update`), and push each instance to Things as a plain to-do.

        Payload shape partially confirmed via mitmproxy 2026-04-20.

        Returns (template_uuid, instance_uuid).

        unit=week  → `fu: 256`, qualifier `of: [{wd: <iso_weekday>}]`
        unit=month → `fu: 8`,   qualifier `of: [{dy: <day_of_month>}]`

        mode=schedule   → template `rr.tp: 0` — next occurrence = anchor + frequency*unit
        mode=completion → template `rr.tp: 1` — next occurrence = <done_date> + frequency*unit
        """
        if unit not in ("week", "month"):
            raise ValueError(f"unit must be 'week' or 'month' (daily/yearly not yet mapped): {unit}")
        if mode not in ("schedule", "completion"):
            raise ValueError(f"mode must be 'schedule' or 'completion': {mode}")

        d = anchor_date or Date.today()
        anchor_ts = int(datetime.combine(d, time.min, tzinfo=timezone.utc).timestamp())

        if unit == "week":
            fu = 256
            of = [{"wd": d.isoweekday()}]
        else:  # month
            fu = 8
            of = [{"dy": d.day}]

        tp_mode = 0 if mode == "schedule" else 1

        rr = {
            "of": of,
            "rrv": 4,
            "tp": tp_mode,
            "fu": fu,
            "sr": anchor_ts,
            "rc": 0,
            "fa": frequency,
            "ts": 0,
            "ia": anchor_ts,
            "ed": 64092211200,
        }

        template_uuid = new_uuid()
        instance_uuid = new_uuid()

        # Template: full Task6 payload with rr populated and st=DEST_SOMEDAY
        template_p = build_new_payload(
            title=title,
            notes=notes,
            project_uuid=project_uuid,
            area_uuid=area_uuid,
            is_project=False,
            destination=DEST_SOMEDAY,
        )
        template_p["rr"] = rr
        template_p["icsd"] = anchor_ts

        # Instance: regular-looking todo pointing at template via `rt`
        instance_p = build_new_payload(
            title=title,
            notes=notes,
            project_uuid=project_uuid,
            area_uuid=area_uuid,
            is_project=False,
            destination=DEST_SOMEDAY,
        )
        instance_p["rt"] = [template_uuid]
        instance_p["sr"] = anchor_ts
        instance_p["tir"] = anchor_ts
        instance_p["icsd"] = anchor_ts

        # Single-commit body with both items — matches the multi-item pattern
        # Things.app uses for this flow.
        body_template = {"t": UPDATE_NEW, "e": "Task6", "p": template_p}
        body_instance = {"t": UPDATE_NEW, "e": "Task6", "p": instance_p}
        self.commit(template_uuid, body_template)
        self.commit(instance_uuid, body_instance)
        return template_uuid, instance_uuid

    def add_area(self, title: str, *, ix: int = 0) -> str:
        """Create a new Area. Confirmed payload shape via mitmproxy capture.

        The `xx` field is Things-internal state; empty `{sn: {}, _t: "oo"}`
        matches what Things.app itself sends.
        """
        uuid = new_uuid()
        payload = {
            "xx": {"sn": {}, "_t": "oo"},
            "ix": ix,
            "tg": [],
            "tt": title,
        }
        body = {"t": UPDATE_NEW, "e": "Area3", "p": payload}
        self.commit(uuid, body)
        return uuid

    def edit_task(
        self,
        uuid: str,
        *,
        title: str | None = None,
        notes: str | None = None,
        when: str | None = None,
        deadline: str | None = None,
        when_set: bool = False,
        deadline_set: bool = False,
        status: int | None = None,
        trashed: bool | None = None,
    ) -> int:
        delta: dict[str, Any] = {}
        if title is not None:
            delta["tt"] = title
        if notes is not None:
            delta["nt"] = default_note(notes)
        if when_set:
            ts = _parse_when(when) if when else None
            delta["sr"] = ts
            delta["tir"] = ts
            if ts is not None:
                delta["st"] = DEST_ANYTIME
        if deadline_set:
            delta["dd"] = _parse_when(deadline) if deadline else None
        if status is not None:
            delta["ss"] = status
            if status in (STATUS_COMPLETE, STATUS_CANCELLED):
                delta["sp"] = int(_now_ts())
            else:
                delta["sp"] = None
        if trashed is not None:
            delta["tr"] = trashed
        if not delta:
            raise ValueError("edit_task called with no fields")
        body = {"t": UPDATE_EDIT, "e": "Task6", "p": build_edit_payload(delta)}
        return self.commit(uuid, body)

    def complete_task(self, uuid: str) -> int:
        return self.edit_task(uuid, status=STATUS_COMPLETE)

    def reopen_task(self, uuid: str) -> int:
        """Mark a completed/cancelled to-do as open again. Shape confirmed via capture."""
        body = {"t": UPDATE_EDIT, "e": "Task6", "p": build_edit_payload({"ss": STATUS_TODO, "sp": None})}
        return self.commit(uuid, body)

    def move_to_project(self, uuid: str, project_uuid: str) -> int:
        """Move a to-do to a different project. Shape: {pr: [<project_uuid>]}."""
        body = {"t": UPDATE_EDIT, "e": "Task6", "p": build_edit_payload({"pr": [project_uuid]})}
        return self.commit(uuid, body)

    def move_to_area(self, uuid: str, area_uuid: str | None) -> int:
        """Move a project to a different area (or to top-level if None). Shape: {ar: [...]}."""
        body = {
            "t": UPDATE_EDIT,
            "e": "Task6",
            "p": build_edit_payload({"ar": [area_uuid] if area_uuid else []}),
        }
        return self.commit(uuid, body)

    def set_tags(self, uuid: str, tag_uuids: list[str]) -> int:
        """Replace the tag set on a to-do or project. Shape: {tg: [<tag_uuid>, ...]}."""
        body = {"t": UPDATE_EDIT, "e": "Task6", "p": build_edit_payload({"tg": list(tag_uuids)})}
        return self.commit(uuid, body)

    def set_index(self, uuid: str, ix: int) -> int:
        """Set the sort index (lower ix = higher in list). Used by atlas reindex."""
        body = {"t": UPDATE_EDIT, "e": "Task6", "p": build_edit_payload({"ix": ix})}
        return self.commit(uuid, body)

    def cancel_task(self, uuid: str) -> int:
        return self.edit_task(uuid, status=STATUS_CANCELLED)

    def reopen_task(self, uuid: str) -> int:
        return self.edit_task(uuid, status=STATUS_TODO)

    def trash_task(self, uuid: str) -> int:
        return self.edit_task(uuid, trashed=True)
