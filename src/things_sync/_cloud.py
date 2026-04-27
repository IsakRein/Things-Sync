"""HTTP client for Things Cloud (cloud.culturedcode.com).

Reverse-engineered protocol — confirmed via mitmproxy captures of Things.app.
The schema constant below is the only pinned version-compat marker; if
commits start returning 4xx, bump it and re-capture.

Wire format (one history "item" per `commit`):

    {"<uuid>": {"t": <0|1>, "e": "Task6"|"Area3", "p": {...}}}

`t=0` is NEW (full payload); `t=1` is EDIT (sparse delta).
`tp` inside `p` distinguishes the kind: 0=todo, 1=project, 2=heading.

This is the **write** side. Reads should go through :class:`ThingsDB`.
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import threading
import time as _time
from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any, ClassVar, Iterable
from urllib.parse import quote

import httpx

from ._log import log_op

SCHEMA = 301
BASE = "https://cloud.culturedcode.com"
API_BASE = f"{BASE}/version/1"

APP_ID = os.environ.get("THINGS_APP_ID", "com.culturedcode.ThingsMac")
USER_AGENT = os.environ.get(
    "THINGS_USER_AGENT",
    "ThingsMac/3.22.2 (Macintosh; Intel Mac OS X 14.4; en_US)",
)

STATE_DIR = Path(os.environ.get("THINGS_STATE_DIR") or Path.home() / ".cache" / "things-sync")
STATE_FILE = STATE_DIR / "state.json"

# Things uses standard Bitcoin Base58 — alphabet without the visually
# ambiguous 0, O, I, l. A UUID with any of those crashes Things.app's
# `decodeBase58String.mapBase58` on receive. Things also asserts the
# decoded value fits in 16 bytes — random 22-char strings overflow ~37%
# of the time and crash the receiver, so we generate a 128-bit int and
# encode it deterministically with leading-pad to 22 chars.
_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _b58_encode(n: int) -> str:
    if n == 0:
        return _ALPHABET[0]
    out: list[str] = []
    base = len(_ALPHABET)
    while n:
        n, r = divmod(n, base)
        out.append(_ALPHABET[r])
    return "".join(reversed(out))


def new_uuid() -> str:
    """22-char Base58-encoded random 128-bit value. Always decodes to ≤16 bytes."""
    return _b58_encode(secrets.randbits(128)).rjust(22, _ALPHABET[0])


# --- entity / status / dest enums (match SQLite + wire) ---------------------

STATUS_OPEN = 0
STATUS_CANCELLED = 2
STATUS_COMPLETE = 3

DEST_INBOX = 0
DEST_ANYTIME = 1
DEST_SOMEDAY = 2

TYPE_TASK = 0
TYPE_PROJECT = 1
TYPE_HEADING = 2

UPDATE_NEW = 0
UPDATE_EDIT = 1
UPDATE_DELETE = 2  # tombstone: empty `p`, no `e` — tells receivers to drop the entity


# --- time codecs ------------------------------------------------------------


def _now_ts() -> float:
    return datetime.now(tz=timezone.utc).timestamp()


def _date_ts(d: Date | datetime | str | None) -> int | None:
    """Things stores dates as midnight-UTC unix ints."""
    if d is None:
        return None
    if isinstance(d, str):
        d = Date.fromisoformat(d)
    if isinstance(d, datetime):
        d = d.date()
    return int(datetime.combine(d, time.min, tzinfo=timezone.utc).timestamp())


# --- payload helpers --------------------------------------------------------


def _default_note(value: str = "") -> dict[str, Any]:
    return {"_t": "tx", "ch": 0, "v": value, "t": 1}


def _default_xx() -> dict[str, Any]:
    return {"_t": "oo", "sn": {}}


def _validate_uuids(label: str, uuids: Iterable[str]) -> list[str]:
    """Reject anything that isn't a Base58-shaped 21-22 char UUID.

    Pushing a non-UUID where Things expects one (e.g. a tag *name* in the
    ``tg`` field) crashes every device that pulls the commit, because the
    Base58 decoder asserts on out-of-alphabet characters. This guards the
    ``add_*`` / ``edit`` API surface; callers must resolve names to
    UUIDs themselves (e.g. via :class:`ThingsDB`).
    """
    out: list[str] = []
    for u in uuids:
        if not isinstance(u, str) or not (21 <= len(u) <= 22):
            raise ValueError(
                f"{label}: expected a Things UUID (21-22 base58 chars), got {u!r}"
            )
        if any(c not in _ALPHABET for c in u):
            raise ValueError(
                f"{label}: {u!r} contains non-base58 character — looks like a name, "
                f"not a UUID. Resolve names to UUIDs before passing them to ThingsCloud."
            )
        out.append(u)
    return out


def _build_payload(
    *,
    title: str,
    tp: int,
    notes: str = "",
    project_uuid: str | None = None,
    area_uuid: str | None = None,
    heading_uuid: str | None = None,
    when_ts: int | None = None,
    deadline_ts: int | None = None,
    destination: int | None = None,
    tags: Iterable[str] = (),
) -> dict[str, Any]:
    """Build a full NEW-payload for any of task/project/heading.

    Field shape pinned against mitmproxy captures of Things.app.app on
    2026-04-26 (heading) and 2026-04-20 (task/project).
    """
    if destination is None:
        destination = DEST_ANYTIME if (project_uuid or area_uuid or when_ts or tp != TYPE_TASK) else DEST_INBOX
    refs = []
    if project_uuid: refs.extend(_validate_uuids("project_uuid", [project_uuid]))
    if area_uuid:    refs.extend(_validate_uuids("area_uuid", [area_uuid]))
    if heading_uuid: refs.extend(_validate_uuids("heading_uuid", [heading_uuid]))
    tag_uuids = _validate_uuids("tags", tags) if tags else []
    now = _now_ts()
    return {
        "ix": 0,
        "tt": title,
        "ss": STATUS_OPEN,
        "st": destination,
        "tr": False,
        "cd": now,
        "md": now,
        "sr": when_ts,
        "tir": when_ts,
        "sp": None,
        "dd": deadline_ts,
        "icp": False,
        "do": 0,
        "lai": None,
        "lt": False,
        "icc": 0,
        "ti": 0,
        "ato": None,
        "icsd": None,
        "rp": None,
        "acrd": None,
        "sb": 0,
        "rr": None,
        "pr": [project_uuid] if project_uuid else [],
        "ar": [area_uuid] if area_uuid else [],
        "agr": [heading_uuid] if heading_uuid else [],
        "tg": tag_uuids,
        "rt": [],
        "rmd": None,
        "dl": [],
        "dds": None,
        "tp": tp,
        "nt": _default_note(notes),
        "xx": _default_xx(),
    }


def _build_edit(fields: dict[str, Any]) -> dict[str, Any]:
    """Sparse EDIT body — only non-None keys are sent. `md` always bumped."""
    out = {k: v for k, v in fields.items() if v is not None}
    out["md"] = _now_ts()
    return out


# --- credentials / account --------------------------------------------------


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


@dataclass
class AccountInfo:
    email: str
    history_key: str
    status: str = ""

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "AccountInfo":
        return cls(
            email=data["email"],
            history_key=data["history-key"],
            status=data.get("status", ""),
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


# --- client state ----------------------------------------------------------


@dataclass
class CloudState:
    """Persisted between runs so commits don't need a full history scan.

    `instance_id` identifies *us* to the server — distinct from any real
    Things.app install so the server fanout doesn't suppress our pushes.
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


# --- the client -------------------------------------------------------------


class ThingsCloud:
    """Synchronous HTTP client for Things Cloud.

    Owns one `httpx.Client` and a small persisted state file at
    ``~/.cache/things-sync/state.json`` (history-key + head_index + an
    instance_id distinct from every real Things install).

    All write operations boil down to ``commit(uuid, body)``. The
    convenience methods (``add_todo``, ``add_heading``, ``edit``, etc.)
    just build the right body and call ``commit``.
    """

    # RLock so commit()'s 409-retry recursion (refresh_head + self-call) can
    # re-enter from the same thread without deadlocking. With a plain Lock,
    # the recursive call blocks forever and wedges every other thread that
    # touches Cloud (the lock is class-level, shared across instances).
    _lock: ClassVar[threading.RLock] = threading.RLock()

    def __init__(self, account: Account, *, timeout: float = 20.0) -> None:
        self.account = account
        self.state = CloudState.load()
        if self.state.history_key and self.state.history_key != account.info.history_key:
            # Account changed — wipe cached cursor.
            self.state = CloudState(history_key=account.info.history_key)
        if not self.state.history_key:
            self.state.history_key = account.info.history_key
        if not self.state.instance_id:
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

        if self.state.head_index == 0:
            self.refresh_head()

        # Optional callback invoked after every successful commit. Used by
        # :class:`ThingsMirror` to keep its local SQLite cache in sync with
        # writes without round-tripping through ``fetch``. Signature:
        # ``(uuid, body, new_head_index)``.
        self._commit_hook = None  # type: ignore[var-annotated]

    @classmethod
    def from_env(cls) -> "ThingsCloud":
        return cls(Account.login(Credentials.from_env()))

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "ThingsCloud":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # ---- raw protocol ----

    def fetch(self, start_index: int | None = None) -> dict[str, Any]:
        """GET /items?start-index=N. Returns raw decoded history."""
        started = _time.time()
        op_id = secrets.token_hex(4)
        idx = self.state.head_index if start_index is None else start_index
        log_op("fetch.start", id=op_id, start_index=idx)
        r = self._client.get("/items", params={"start-index": str(idx)})
        ms = int((_time.time() - started) * 1000)
        if r.status_code >= 400:
            log_op(
                "fetch.error", id=op_id, status=r.status_code, ms=ms,
                body_preview=r.text[:300],
            )
            raise CloudError(f"Fetch failed [{r.status_code}]: {r.text[:300]}")
        data = r.json()
        items = data.get("items", []) or []
        log_op(
            "fetch.ok", id=op_id, ms=ms,
            head=data.get("current-item-index"), n_items=len(items),
        )
        return data

    def refresh_head(self) -> int:
        """Bootstrap (or re-bootstrap) the cached head_index from the server."""
        data = self.fetch(start_index=0)
        head = int(data["current-item-index"])
        self.state.head_index = head
        self.state.save()
        return head

    def replay(self) -> dict[str, dict[str, Any]]:
        """Fetch full history → ``{uuid: item_state}`` map.

        Each value carries the merged payload after replaying NEW + EDIT
        entries, with an extra ``_e`` key holding the entity name (e.g.
        ``"Task6"``, ``"Area3"``, ``"Tag4"``, ``"Tombstone2"``). Use the
        item's ``tp`` to distinguish task / project / heading.

        Server-authoritative read — sees commits the moment they post,
        regardless of whether Mac has pulled them yet. Use sparingly:
        scans the entire history, so it's slow on big accounts.
        """
        data = self.fetch(start_index=0)
        out: dict[str, dict[str, Any]] = {}
        for entry in data.get("items", []):
            for uuid, body in entry.items():
                p = body.get("p", {}) or {}
                if uuid not in out:
                    out[uuid] = {"_e": body.get("e", "")}
                if body.get("t") == 0:  # NEW
                    out[uuid].update(p)
                    out[uuid]["_e"] = body.get("e", "")
                else:  # EDIT
                    out[uuid].update(p)
        return out

    def commit(self, item_uuid: str, body: dict[str, Any], *, _retry: int = 4) -> int:
        """POST /commit. Refresh head + retry on stale-ancestor (409/410/412).

        ``atlas watch`` and Things' Mac/iOS sync pollers can commit
        concurrently — a single retry isn't enough when Mac is in a
        burst. We do up to ``_retry`` attempts with short backoff
        (50/150/350/750 ms) before raising.

        Every step (start, retry, ok, error, exhausted) is recorded
        in ``~/.cache/things-sync/ops.jsonl`` via :func:`log_op` for
        post-mortem attribution.
        """
        import random as _random
        started = _time.time()
        op_id = secrets.token_hex(4)
        log_op(
            "commit.start", id=op_id, uuid=item_uuid,
            t=body.get("t"), e=body.get("e"),
            ancestor_index=self.state.head_index,
            p=body.get("p") or {},
        )
        with self._lock:
            for attempt in range(_retry + 1):
                params = {"ancestor-index": str(self.state.head_index), "_cnt": "1"}
                payload = {item_uuid: body}
                r = self._client.post("/commit", params=params, json=payload)
                if r.status_code in (409, 410, 412) and attempt < _retry:
                    prev_head = self.state.head_index
                    self.refresh_head()
                    # Exponential backoff with jitter so concurrent watchers
                    # / pollers don't dogpile the same head_index.
                    sleep_s = 0.05 * (3 ** attempt) + _random.uniform(0, 0.05)
                    log_op(
                        "commit.retry", id=op_id, status=r.status_code,
                        attempt=attempt + 1, prev_head=prev_head,
                        new_head=self.state.head_index,
                        sleep_ms=int(sleep_s * 1000),
                    )
                    _time.sleep(sleep_s)
                    continue
                ms = int((_time.time() - started) * 1000)
                if r.status_code >= 400:
                    log_op(
                        "commit.error", id=op_id, status=r.status_code,
                        attempts=attempt + 1, ms=ms,
                        body_preview=r.text[:300],
                    )
                    raise CloudError(
                        f"Commit failed [{r.status_code}] after "
                        f"{attempt + 1} attempt(s): {r.text[:300]}"
                    )
                data = r.json()
                new_head = int(data["server-head-index"])
                self.state.head_index = new_head
                self.state.save()
                if self._commit_hook is not None:
                    self._commit_hook(item_uuid, body, new_head)
                log_op(
                    "commit.ok", id=op_id, head=new_head,
                    attempts=attempt + 1, ms=ms,
                )
                return new_head
            log_op(
                "commit.exhausted", id=op_id,
                ms=int((_time.time() - started) * 1000),
            )
            raise CloudError("commit: exhausted retries without resolving")

    # ---- create ----

    def add_todo(
        self,
        title: str,
        *,
        notes: str = "",
        when: Date | datetime | str | None = None,
        deadline: Date | datetime | str | None = None,
        project: str | None = None,
        area: str | None = None,
        heading: str | None = None,
        tags: Iterable[str] = (),
    ) -> str:
        uuid = new_uuid()
        p = _build_payload(
            title=title, tp=TYPE_TASK, notes=notes,
            project_uuid=project, area_uuid=area, heading_uuid=heading,
            when_ts=_date_ts(when), deadline_ts=_date_ts(deadline), tags=tags,
        )
        self.commit(uuid, {"t": UPDATE_NEW, "e": "Task6", "p": p})
        return uuid

    def add_project(
        self,
        title: str,
        *,
        notes: str = "",
        deadline: Date | datetime | str | None = None,
        area: str | None = None,
        tags: Iterable[str] = (),
    ) -> str:
        uuid = new_uuid()
        p = _build_payload(
            title=title, tp=TYPE_PROJECT, notes=notes,
            area_uuid=area, deadline_ts=_date_ts(deadline), tags=tags,
            destination=DEST_ANYTIME,
        )
        self.commit(uuid, {"t": UPDATE_NEW, "e": "Task6", "p": p})
        return uuid

    def add_heading(self, title: str, *, project: str) -> str:
        """Create a heading inside ``project``. Mac/iOS picks it up on next sync."""
        uuid = new_uuid()
        p = _build_payload(title=title, tp=TYPE_HEADING, project_uuid=project)
        self.commit(uuid, {"t": UPDATE_NEW, "e": "Task6", "p": p})
        return uuid

    def add_area(self, title: str, *, ix: int = 0) -> str:
        uuid = new_uuid()
        p = {"xx": _default_xx(), "ix": ix, "tg": [], "tt": title}
        self.commit(uuid, {"t": UPDATE_NEW, "e": "Area3", "p": p})
        return uuid

    # ---- edit / status / move ----

    def edit(
        self,
        uuid: str,
        *,
        title: str | None = None,
        notes: str | None = None,
        when: Date | datetime | str | None | bool = False,
        deadline: Date | datetime | str | None | bool = False,
        status: int | None = None,
        trashed: bool | None = None,
        project: str | None | bool = False,
        area: str | None | bool = False,
        heading: str | None | bool = False,
        tags: Iterable[str] | None = None,
        index: int | None = None,
        destination: int | None = None,
    ) -> int:
        """Patch a Task6 entity (todo, project, or heading).

        For nullable fields (``when``, ``deadline``, ``project``, ``area``,
        ``heading``) pass ``None`` to clear, the new value to set, or leave
        as the default sentinel ``False`` to leave alone.
        """
        delta: dict[str, Any] = {}
        if title is not None:
            delta["tt"] = title
        if notes is not None:
            delta["nt"] = _default_note(notes)
        if when is not False:
            ts = _date_ts(when) if when else None
            delta["sr"] = ts
            delta["tir"] = ts
            if ts is not None:
                delta["st"] = DEST_ANYTIME
        if deadline is not False:
            delta["dd"] = _date_ts(deadline) if deadline else None
        if status is not None:
            delta["ss"] = status
            delta["sp"] = int(_now_ts()) if status in (STATUS_COMPLETE, STATUS_CANCELLED) else None
        if trashed is not None:
            delta["tr"] = trashed
        if project is not False:
            delta["pr"] = _validate_uuids("project", [project]) if project else []
        if area is not False:
            delta["ar"] = _validate_uuids("area", [area]) if area else []
        if heading is not False:
            delta["agr"] = _validate_uuids("heading", [heading]) if heading else []
        if tags is not None:
            delta["tg"] = _validate_uuids("tags", tags)
        if index is not None:
            delta["ix"] = index
        if destination is not None:
            delta["st"] = destination
        if not delta:
            raise ValueError("edit called with no fields to change")
        return self.commit(uuid, {"t": UPDATE_EDIT, "e": "Task6", "p": _build_edit(delta)})

    # convenience wrappers
    def complete(self, uuid: str) -> int: return self.edit(uuid, status=STATUS_COMPLETE)
    def cancel(self, uuid: str) -> int:   return self.edit(uuid, status=STATUS_CANCELLED)
    def reopen(self, uuid: str) -> int:   return self.edit(uuid, status=STATUS_OPEN)
    def trash(self, uuid: str) -> int:    return self.edit(uuid, trashed=True)
    def untrash(self, uuid: str) -> int:  return self.edit(uuid, trashed=False)

    def delete(self, uuid: str, *, entity: str = "Task6") -> int:
        """Hard-delete an entity via a ``t=2`` tombstone commit.

        Goes around the soft-trash path (``tr=True``) entirely — the
        entity is removed, not merely flagged. For todos/projects you
        usually want :meth:`trash` so the user can recover from the
        Trash list; for headings ``tr=True`` is silently ignored by
        Things' UI, so :meth:`delete` is the only verb that works.

        ``entity`` MUST be set on the wire — Things' pull-side parser
        crashes (``SCChangeMapCreateWithPropertyList`` → process
        terminate) on a ``t=2`` commit missing the ``e`` field, and the
        bad commit then poisons every device on the account because
        cloud history is append-only. We default ``entity="Task6"``
        because that's what Things itself emits for todo / project /
        heading deletes, and we hard-assert non-empty here so a future
        caller can't drop it again.
        """
        if not entity:
            raise ValueError(
                "delete: `entity` must be set on a t=2 tombstone — a missing "
                "`e` field crashes Things on pull and poisons the entire account."
            )
        return self.commit(uuid, {"t": UPDATE_DELETE, "e": entity, "p": {}})

    def trash_heading(self, uuid: str) -> int:
        """Delete a heading. Things has no Trash view for headings —
        ``tr=True`` is silently ignored by the UI — so we route this
        through :meth:`delete` (a ``t=2`` tombstone commit) which is
        what Things itself emits on a manual heading delete."""
        return self.delete(uuid)

    def clear_due_date(self, uuid: str) -> int:
        """Clear a due date — the one operation AppleScript can't do."""
        return self.edit(uuid, deadline=None)
