"""Local SQLite mirror of Things Cloud history.

Pulls deltas via :meth:`ThingsCloud.fetch` and replays them into a
SQLite file we own — independent of the Things app's own database. This
lets reads stop depending on whether Things is running or has synced
yet; reads come from disk-speed local SQLite that *we* keep current
against cloud directly.

The mirror stores one row per entity (todo / project / heading / area /
tag / contact / tombstone) keyed by UUID, with the latest-merged JSON
payload from the wire format. Deltas are applied in order and a
single ``applied_index`` cursor tracks how far we've replayed.

The typed accessors (``todos``, ``projects``, ``areas``, ``tags``, …)
mirror the surface of :class:`ThingsDB` so existing call sites can swap
to a cloud-backed source without changes.

Writes are hooked through :attr:`ThingsCloud._commit_hook`: every
successful ``cloud.commit`` lands locally too, so reads see the new
state immediately — no pull, no Things.app, no sync wait.

The mirror's cursor (``applied_index`` in the ``meta`` table) is
separate from :class:`CloudState.head_index` — that one is the
write-side ancestor cursor advanced by ``commit``; this one only
advances when we apply items (pulled or hooked).
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time as _time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from ._cloud import STATE_DIR, ThingsCloud
from ._log import log_op
from .models import Area, Heading, Project, StartBucket, Status, Tag, Todo

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entities (
    uuid       TEXT PRIMARY KEY,
    entity     TEXT NOT NULL,
    tp         INTEGER,
    payload    TEXT NOT NULL,
    last_index INTEGER NOT NULL,
    deleted    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS entities_entity    ON entities(entity);
CREATE INDEX IF NOT EXISTS entities_entity_tp ON entities(entity, tp);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


_TYPE_TODO = 0
_TYPE_PROJECT = 1
_TYPE_HEADING = 2

_STATUS_BY_INT = {0: Status.OPEN, 2: Status.CANCELED, 3: Status.COMPLETED}
_STATUS_OPEN = 0
_STATUS_CANCELED = 2
_STATUS_COMPLETED = 3


class ThingsMirror:
    """Local SQLite cache of Things Cloud history.

    Wraps a :class:`ThingsCloud` to pull deltas. The DB lives at
    ``~/.cache/things-sync/mirror.sqlite`` by default and is safe to
    share between processes (one writer at a time via the SQLite lock).

    On construction, registers itself as ``cloud._commit_hook`` so every
    successful write also lands locally; reads see writes immediately
    without a fetch round-trip.
    """

    def __init__(
        self,
        cloud: ThingsCloud,
        *,
        path: Path | None = None,
        attach: bool = True,
    ) -> None:
        self.cloud = cloud
        self.path = path or STATE_DIR / "mirror.sqlite"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # RLock so the post-commit hook can call pull() (which also takes
        # the lock) without deadlocking.
        self._lock = threading.RLock()
        self._init_schema()
        self._guard_history_key()
        if attach:
            cloud._commit_hook = self._on_commit

    # ---- schema / meta ------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with closing(self._connect()) as conn:
            conn.executescript(_SCHEMA)
            self._set_meta(conn, "schema_version", str(SCHEMA_VERSION))

    def _guard_history_key(self) -> None:
        """Wipe the mirror if the cloud account changed underneath us.

        Same protection :class:`CloudState` does — a stale mirror bound
        to a different account would replay the wrong commits.
        """
        cur_key = self.cloud.account.info.history_key
        with closing(self._connect()) as conn:
            stored = self._get_meta(conn, "history_key")
            if stored and stored != cur_key:
                conn.execute("DELETE FROM entities")
                self._set_meta(conn, "applied_index", "0")
            self._set_meta(conn, "history_key", cur_key)

    @staticmethod
    def _get_meta(conn: sqlite3.Connection, key: str) -> str | None:
        row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    @staticmethod
    def _set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
        conn.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    @property
    def applied_index(self) -> int:
        with closing(self._connect()) as conn:
            v = self._get_meta(conn, "applied_index")
            return int(v) if v else 0

    # ---- pull ---------------------------------------------------------------

    def pull(self) -> int:
        """Fetch new items since ``applied_index`` and apply them.

        Returns the number of items applied. Safe to call repeatedly;
        a no-op when the mirror is already caught up.
        """
        started = _time.time()
        with self._lock:
            start = self.applied_index
            log_op("mirror.pull.start", from_index=start)
            try:
                data = self.cloud.fetch(start_index=start)
                items = data.get("items", []) or []
                new_head = int(data.get("current-item-index", start + len(items)))
                with closing(self._connect()) as conn:
                    conn.execute("BEGIN")
                    try:
                        idx = start
                        for entry in items:
                            idx += 1
                            for uuid, body in entry.items():
                                self._apply(conn, idx, uuid, body)
                        self._set_meta(conn, "applied_index", str(new_head))
                        conn.execute("COMMIT")
                    except Exception:
                        conn.execute("ROLLBACK")
                        raise
            except Exception as e:
                log_op(
                    "mirror.pull.error",
                    from_index=start,
                    error=f"{type(e).__name__}: {e}",
                    ms=int((_time.time() - started) * 1000),
                )
                raise
            log_op(
                "mirror.pull.ok",
                from_index=start,
                applied=len(items),
                head=new_head,
                ms=int((_time.time() - started) * 1000),
            )
            return len(items)

    def reset(self) -> int:
        """Wipe the mirror and replay from index 0. Returns items applied."""
        with self._lock:
            with closing(self._connect()) as conn:
                conn.execute("DELETE FROM entities")
                self._set_meta(conn, "applied_index", "0")
        return self.pull()

    def _apply(
        self,
        conn: sqlite3.Connection,
        idx: int,
        uuid: str,
        body: dict[str, Any],
    ) -> None:
        t = body.get("t")
        entity = body.get("e", "") or ""
        p = body.get("p", {}) or {}
        # ``t=2`` is a tombstone commit (empty payload, no entity name).
        # ``e == "Tombstone2"`` is the older entity-shaped variant we've
        # also seen on the wire. Either way: drop the row from default
        # reads.
        is_delete = (t == 2) or entity == "Tombstone2"

        row = conn.execute(
            "SELECT entity, tp, payload, deleted FROM entities WHERE uuid=?",
            (uuid,),
        ).fetchone()

        if row is None:
            payload = dict(p)
            tp = payload.get("tp")
            conn.execute(
                "INSERT INTO entities(uuid, entity, tp, payload, last_index, deleted) "
                "VALUES(?,?,?,?,?,?)",
                (uuid, entity, tp, json.dumps(payload), idx, int(is_delete)),
            )
            return

        merged = json.loads(row["payload"])
        if t == 0:
            merged = dict(p)
            new_entity = entity or row["entity"]
        else:
            # Things-Mac echoes diagnostic-only CRDT markers like
            # ``{"_t": "tx", "t": 0, "diag": "apply"}`` for unchanged
            # text fields (notes, etc). Blindly replacing the field
            # would wipe its real value (the marker has no ``v``).
            # Treat any ``_t=tx``+``t=0`` dict as a no-op for that key.
            for k, v in list(p.items()):
                if (
                    isinstance(v, dict)
                    and v.get("_t") == "tx"
                    and v.get("t") == 0
                    and "v" not in v
                ):
                    p = {kk: vv for kk, vv in p.items() if kk != k}
            merged.update(p)
            new_entity = row["entity"] or entity
        new_tp = merged.get("tp", row["tp"])
        deleted = 1 if is_delete else row["deleted"]
        conn.execute(
            "UPDATE entities SET entity=?, tp=?, payload=?, last_index=?, deleted=? "
            "WHERE uuid=?",
            (new_entity, new_tp, json.dumps(merged), idx, deleted, uuid),
        )

    # ---- read accessors -----------------------------------------------------

    def get(self, uuid: str) -> dict[str, Any] | None:
        """Return ``{uuid, entity, tp, payload, last_index, deleted}`` or None."""
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM entities WHERE uuid=?", (uuid,)
            ).fetchone()
        if row is None:
            return None
        return self._row_to_dict(row)

    def all(
        self,
        entity: str | None = None,
        tp: int | None = None,
        *,
        include_deleted: bool = False,
    ) -> list[dict[str, Any]]:
        """List entities filtered by entity name and/or ``tp``."""
        sql = "SELECT * FROM entities WHERE 1=1"
        args: list[Any] = []
        if entity is not None:
            sql += " AND entity=?"; args.append(entity)
        if tp is not None:
            sql += " AND tp=?"; args.append(tp)
        if not include_deleted:
            sql += " AND deleted=0"
        with closing(self._connect()) as conn:
            rows = conn.execute(sql, args).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def counts(self) -> dict[str, int]:
        """``{entity: count}`` over non-deleted rows."""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT entity, COUNT(*) AS n FROM entities "
                "WHERE deleted=0 GROUP BY entity"
            ).fetchall()
        return {r["entity"]: r["n"] for r in rows}

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "uuid": row["uuid"],
            "entity": row["entity"],
            "tp": row["tp"],
            "payload": json.loads(row["payload"]),
            "last_index": row["last_index"],
            "deleted": bool(row["deleted"]),
        }

    # ---- write hook ---------------------------------------------------------

    def _on_commit(self, uuid: str, body: dict[str, Any], new_head: int) -> None:
        """Apply a freshly-committed item to the mirror.

        Fast path: when we were already caught up (``applied_index + 1
        == new_head``) the commit is the only new item, so we apply
        directly without an extra fetch. Otherwise, pull from cloud —
        catches us up *including* our own commit.
        """
        with self._lock:
            applied = self.applied_index
            log_op(
                "mirror.on_commit", uuid=uuid, t=body.get("t"),
                e=body.get("e"), applied=applied, new_head=new_head,
                fast_path=(applied + 1 == new_head),
            )
            if applied + 1 == new_head:
                with closing(self._connect()) as conn:
                    conn.execute("BEGIN")
                    try:
                        self._apply(conn, new_head, uuid, body)
                        self._set_meta(conn, "applied_index", str(new_head))
                        conn.execute("COMMIT")
                    except Exception:
                        conn.execute("ROLLBACK")
                        raise
            else:
                self.pull()

    # ---- typed reads (parity with ThingsDB) ---------------------------------

    def todos(self, *, include_trashed: bool = False) -> list[Todo]:
        """Every non-trashed todo, any status. Ordered by ``ix``."""
        return self._tasks(_TYPE_TODO, include_trashed, _todo_from_payload)

    def projects(self, *, include_trashed: bool = False) -> list[Project]:
        return self._tasks(_TYPE_PROJECT, include_trashed, _project_from_payload)

    def headings(self, *, include_trashed: bool = False) -> list[Heading]:
        rows = self._task_rows(_TYPE_HEADING, include_trashed)
        return [_heading_from_payload(uuid, p) for uuid, p in rows]

    def areas(self) -> list[Area]:
        tag_names = self._tag_name_map()
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT uuid, payload FROM entities WHERE entity='Area3' AND deleted=0"
            ).fetchall()
        items = [(r["uuid"], json.loads(r["payload"])) for r in rows]
        items.sort(key=lambda x: x[1].get("ix", 0))
        return [_area_from_payload(uuid, p, tag_names) for uuid, p in items]

    def tags(self) -> list[Tag]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT uuid, payload FROM entities WHERE entity='Tag4' AND deleted=0"
            ).fetchall()
        items = [(r["uuid"], json.loads(r["payload"])) for r in rows]
        items.sort(key=lambda x: x[1].get("ix", 0))
        return [_tag_from_payload(uuid, p) for uuid, p in items]

    # ----- by-id lookups -----

    def todo(self, id: str, *, include_trashed: bool = True) -> Todo | None:
        return self._task_by_id(id, _TYPE_TODO, include_trashed, _todo_from_payload)

    def project(self, id: str, *, include_trashed: bool = True) -> Project | None:
        return self._task_by_id(id, _TYPE_PROJECT, include_trashed, _project_from_payload)

    def heading(self, id: str, *, include_trashed: bool = True) -> Heading | None:
        row = self._task_row_by_id(id, _TYPE_HEADING, include_trashed)
        return _heading_from_payload(*row) if row else None

    def area(self, id: str) -> Area | None:
        with closing(self._connect()) as conn:
            r = conn.execute(
                "SELECT uuid, payload FROM entities "
                "WHERE entity='Area3' AND uuid=? AND deleted=0",
                (id,),
            ).fetchone()
        if r is None:
            return None
        return _area_from_payload(r["uuid"], json.loads(r["payload"]), self._tag_name_map())

    def tag(self, name: str) -> Tag | None:
        for t in self.tags():
            if t.name == name:
                return t
        return None

    def tag_by_id(self, id: str) -> Tag | None:
        with closing(self._connect()) as conn:
            r = conn.execute(
                "SELECT uuid, payload FROM entities "
                "WHERE entity='Tag4' AND uuid=? AND deleted=0",
                (id,),
            ).fetchone()
        if r is None:
            return None
        return _tag_from_payload(r["uuid"], json.loads(r["payload"]))

    # ----- filtered todos -----

    def todos_in_project(self, project_id: str, *, include_trashed: bool = False) -> list[Todo]:
        return [t for t in self.todos(include_trashed=include_trashed)
                if t.project_id == project_id]

    def todos_in_area(self, area_id: str, *, include_trashed: bool = False) -> list[Todo]:
        return [t for t in self.todos(include_trashed=include_trashed)
                if t.area_id == area_id]

    def todos_under_heading(self, heading_id: str, *, include_trashed: bool = False) -> list[Todo]:
        return [t for t in self.todos(include_trashed=include_trashed)
                if t.heading_id == heading_id]

    def todos_with_tag(self, name: str, *, include_trashed: bool = False) -> list[Todo]:
        return [t for t in self.todos(include_trashed=include_trashed)
                if name in t.tag_names]

    # ----- counts / exists -----

    def count_todos(self, *, include_trashed: bool = False) -> int:
        return self._count(_TYPE_TODO, include_trashed)

    def count_projects(self, *, include_trashed: bool = False) -> int:
        return self._count(_TYPE_PROJECT, include_trashed)

    def count_areas(self) -> int:
        with closing(self._connect()) as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM entities WHERE entity='Area3' AND deleted=0"
            ).fetchone()[0]

    def count_tags(self) -> int:
        with closing(self._connect()) as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM entities WHERE entity='Tag4' AND deleted=0"
            ).fetchone()[0]

    def exists(self, id: str) -> bool:
        with closing(self._connect()) as conn:
            r = conn.execute(
                "SELECT 1 FROM entities WHERE uuid=? AND deleted=0 LIMIT 1", (id,)
            ).fetchone()
        return r is not None

    # ---- typed-read helpers -------------------------------------------------

    def _tag_name_map(self) -> dict[str, str]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT uuid, payload FROM entities WHERE entity='Tag4' AND deleted=0"
            ).fetchall()
        return {r["uuid"]: (json.loads(r["payload"]).get("tt") or "") for r in rows}

    def _task_rows(
        self, tp: int, include_trashed: bool
    ) -> list[tuple[str, dict[str, Any]]]:
        sql = "SELECT uuid, payload FROM entities WHERE entity='Task6' AND tp=? AND deleted=0"
        with closing(self._connect()) as conn:
            rows = conn.execute(sql, (tp,)).fetchall()
        items: list[tuple[str, dict[str, Any]]] = []
        for r in rows:
            p = json.loads(r["payload"])
            if not include_trashed and p.get("tr"):
                continue
            items.append((r["uuid"], p))
        items.sort(key=lambda x: x[1].get("ix", 0))
        return items

    def _tasks(
        self,
        tp: int,
        include_trashed: bool,
        parse: Callable[[str, dict[str, Any], dict[str, str]], Any],
    ) -> list[Any]:
        rows = self._task_rows(tp, include_trashed)
        tag_names = self._tag_name_map()
        return [parse(uuid, p, tag_names) for uuid, p in rows]

    def _task_row_by_id(
        self, id: str, tp: int, include_trashed: bool
    ) -> tuple[str, dict[str, Any]] | None:
        with closing(self._connect()) as conn:
            r = conn.execute(
                "SELECT uuid, payload FROM entities "
                "WHERE entity='Task6' AND tp=? AND uuid=? AND deleted=0",
                (tp, id),
            ).fetchone()
        if r is None:
            return None
        p = json.loads(r["payload"])
        if not include_trashed and p.get("tr"):
            return None
        return (r["uuid"], p)

    def _task_by_id(
        self,
        id: str,
        tp: int,
        include_trashed: bool,
        parse: Callable[[str, dict[str, Any], dict[str, str]], Any],
    ) -> Any:
        row = self._task_row_by_id(id, tp, include_trashed)
        if row is None:
            return None
        return parse(row[0], row[1], self._tag_name_map())

    def _count(self, tp: int, include_trashed: bool) -> int:
        return len(self._task_rows(tp, include_trashed))


# ---- payload → dataclass mappers ------------------------------------------


def _from_unix(v: float | None) -> datetime | None:
    if v is None:
        return None
    try:
        return datetime.fromtimestamp(v)
    except (OverflowError, OSError, ValueError):
        return None


def _date_from_unix_midnight(ts: int | None) -> datetime | None:
    """Wire format stores `dd`/`tir` as midnight-UTC unix ints. Decode to
    a naive midnight datetime, matching :func:`_db._decode_packed_date`."""
    if not ts:
        return None
    try:
        utc = datetime.fromtimestamp(ts, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    return datetime(utc.year, utc.month, utc.day)


def _notes(payload: dict[str, Any]) -> str:
    nt = payload.get("nt") or {}
    if isinstance(nt, dict):
        return nt.get("v") or ""
    return ""


def _start_bucket(v: int | None) -> StartBucket:
    try:
        return StartBucket(v)
    except (ValueError, TypeError):
        return StartBucket.ANYTIME


def _resolve_tag_names(uuids: Iterable[str], tag_names: dict[str, str]) -> tuple[str, ...]:
    out = [tag_names[u] for u in uuids if u in tag_names and tag_names[u]]
    return tuple(out)


def _todo_from_payload(uuid: str, p: dict[str, Any], tag_names: dict[str, str]) -> Todo:
    ss = p.get("ss", 0)
    status = _STATUS_BY_INT.get(ss, Status.OPEN)
    sp = _from_unix(p.get("sp"))
    return Todo(
        id=uuid,
        name=p.get("tt") or "",
        notes=_notes(p),
        status=status,
        due_date=_date_from_unix_midnight(p.get("dd")),
        activation_date=_date_from_unix_midnight(p.get("sr")),
        completion_date=sp if ss == _STATUS_COMPLETED else None,
        cancellation_date=sp if ss == _STATUS_CANCELED else None,
        creation_date=_from_unix(p.get("cd")),
        modification_date=_from_unix(p.get("md")),
        tag_names=_resolve_tag_names(p.get("tg") or (), tag_names),
        project_id=(p.get("pr") or [None])[0],
        area_id=(p.get("ar") or [None])[0],
        contact_id=None,  # contact wire format not yet captured
        heading_id=(p.get("agr") or [None])[0],
        start_bucket=_start_bucket(p.get("st")),
    )


def _project_from_payload(uuid: str, p: dict[str, Any], tag_names: dict[str, str]) -> Project:
    ss = p.get("ss", 0)
    status = _STATUS_BY_INT.get(ss, Status.OPEN)
    sp = _from_unix(p.get("sp"))
    return Project(
        id=uuid,
        name=p.get("tt") or "",
        notes=_notes(p),
        status=status,
        due_date=_date_from_unix_midnight(p.get("dd")),
        activation_date=_date_from_unix_midnight(p.get("sr")),
        completion_date=sp if ss == _STATUS_COMPLETED else None,
        cancellation_date=sp if ss == _STATUS_CANCELED else None,
        creation_date=_from_unix(p.get("cd")),
        modification_date=_from_unix(p.get("md")),
        tag_names=_resolve_tag_names(p.get("tg") or (), tag_names),
        area_id=(p.get("ar") or [None])[0],
    )


def _heading_from_payload(uuid: str, p: dict[str, Any]) -> Heading:
    return Heading(
        id=uuid,
        name=p.get("tt") or "",
        project_id=(p.get("pr") or [None])[0],
        status=_STATUS_BY_INT.get(p.get("ss", 0), Status.OPEN),
    )


def _area_from_payload(uuid: str, p: dict[str, Any], tag_names: dict[str, str]) -> Area:
    return Area(
        id=uuid,
        name=p.get("tt") or "",
        tag_names=_resolve_tag_names(p.get("tg") or (), tag_names),
        # `collapsed` not in wire format; match :class:`ThingsDB` default.
        collapsed=False,
    )


def _tag_from_payload(uuid: str, p: dict[str, Any]) -> Tag:
    parents = p.get("pn") or []
    return Tag(
        id=uuid,
        name=p.get("tt") or "",
        parent_id=parents[0] if parents else None,
        keyboard_shortcut=p.get("sh") or "",
    )
