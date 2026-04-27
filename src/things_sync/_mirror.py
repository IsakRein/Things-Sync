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

Schema is intentionally simple — UUID + entity name + ``tp`` + raw
payload blob + the last history index that touched it. Higher-level
typed accessors can be layered on later by reading the JSON column.

The mirror's cursor (``applied_index`` in the ``meta`` table) is
separate from :class:`CloudState.head_index` — that one is the
write-side ancestor cursor advanced by ``commit``; this one only
advances when we apply pulled items.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import closing
from pathlib import Path
from typing import Any, Iterable

from ._cloud import STATE_DIR, ThingsCloud

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


class ThingsMirror:
    """Local SQLite cache of Things Cloud history.

    Wraps a :class:`ThingsCloud` to pull deltas. The DB lives at
    ``~/.cache/things-sync/mirror.sqlite`` by default and is safe to
    share between processes (one writer at a time via the SQLite lock).
    """

    _lock = threading.Lock()

    def __init__(self, cloud: ThingsCloud, *, path: Path | None = None) -> None:
        self.cloud = cloud
        self.path = path or STATE_DIR / "mirror.sqlite"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()
        self._guard_history_key()

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
        with self._lock:
            start = self.applied_index
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
        is_tombstone = entity == "Tombstone2"

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
                (uuid, entity, tp, json.dumps(payload), idx, int(is_tombstone)),
            )
            return

        merged = json.loads(row["payload"])
        if t == 0:
            merged = dict(p)
            new_entity = entity or row["entity"]
        else:
            merged.update(p)
            new_entity = row["entity"] or entity
        new_tp = merged.get("tp", row["tp"])
        deleted = 1 if is_tombstone else row["deleted"]
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
