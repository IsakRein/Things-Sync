"""Read-only access to Things 3's on-disk SQLite database.

Things keeps its state in a WAL-mode SQLite file at::

    ~/Library/Group Containers/JLMPQHK86H.com.culturedcode.ThingsMac/
        ThingsData-*/Things Database.thingsdatabase/main.sqlite

Enumerating entities through AppleScript is dominated by per-property IPC
roundtrips — a few hundred milliseconds per item once you read more than a
handful of fields. The same data read from SQLite comes back in
milliseconds regardless of count. For any read-heavy workload (sync
planning, reporting, bulk diffs) this is 100–1000× faster than
:class:`Things`.

Reads only. Writes must still go through :class:`Things` — poking Cultured
Code's private store directly would desynchronise Things' in-memory state
and its CloudKit sync. WAL mode makes concurrent reads safe while the app
is running.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from .models import Area, Project, StartBucket, Status, Tag, Todo


_DB_GLOB = (
    "Library/Group Containers/JLMPQHK86H.com.culturedcode.ThingsMac/"
    "ThingsData-*/Things Database.thingsdatabase/main.sqlite"
)

# TMTask.type
_TYPE_TODO = 0
_TYPE_PROJECT = 1

# TMTask.status — the ints Things uses on disk, distinct from our Status enum.
_STATUS_BY_INT = {0: Status.OPEN, 2: Status.CANCELED, 3: Status.COMPLETED}
_STATUS_CANCELED = 2
_STATUS_COMPLETED = 3


def _default_db_path() -> Path:
    """Locate the Things 3 SQLite file in the current user's Library.

    Things writes to a container-ID directory that changes between
    installs, so we glob. Multiple ThingsData-* directories can exist if
    the app has been reinstalled; we take the most recently modified one.
    """
    candidates = list(Path.home().glob(_DB_GLOB))
    if not candidates:
        raise FileNotFoundError(
            "Things 3 database not found under ~/Library/Group Containers. "
            "Pass `path=` explicitly if your install is non-standard."
        )
    candidates.sort(key=lambda p: p.stat().st_mtime)
    return candidates[-1]


def _decode_packed_date(v: int | None) -> datetime | None:
    """Things packs `deadline` / `startDate` as a 32-bit int:
    year in bits 16+, month in 12–15, day in 7–11. 0 / NULL = unset.

    Returns a midnight datetime to match the AppleScript reader (which
    rounds through Things' own isoDate serializer with zeroed H/M/S).
    """
    if not v:
        return None
    y = v >> 16
    m = (v >> 12) & 0xF
    d = (v >> 7) & 0x1F
    if not (1 <= m <= 12 and 1 <= d <= 31 and y >= 1970):
        return None
    try:
        return datetime(y, m, d)
    except ValueError:
        return None


def _start_bucket(v: int | None) -> StartBucket:
    """Map TMTask.start (0/1/2) onto the StartBucket enum.

    Unexpected values fall through to ANYTIME — Things' internal schema has
    historically grown new codes, and leaving a foreign value unpinned would
    crash every reader the next time Cultured Code ships one.
    """
    try:
        return StartBucket(v)
    except ValueError:
        return StartBucket.ANYTIME


def _from_unix(v: float | None) -> datetime | None:
    if v is None:
        return None
    try:
        return datetime.fromtimestamp(v)
    except (OverflowError, OSError, ValueError):
        return None


class ThingsDB:
    """Read-only view onto Things' SQLite database.

    Mirrors the read surface of :class:`Things` (``todos``, ``projects``,
    ``areas``, ``tags``) but reads directly from disk. Returns the same
    dataclasses from :mod:`things_sync.models`, so call sites can swap
    between the two readers without downstream changes.

    Parameters
    ----------
    path
        Path to ``main.sqlite``. Autodetected in the default Things
        container if omitted.
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else _default_db_path()

    # ------------------------------------------------------------ internals

    def _connect(self) -> sqlite3.Connection:
        # `mode=ro` opens read-only and never touches the file. We avoid
        # `immutable=1` because Things mutates the DB while we're reading.
        uri = f"file:{self.path}?mode=ro"
        con = sqlite3.connect(uri, uri=True)
        con.row_factory = sqlite3.Row
        return con

    # --------------------------------------------------------------- reads

    def todos(self, *, include_trashed: bool = False) -> list[Todo]:
        """Every non-trashed todo (type=0), any status.

        Pass ``include_trashed=True`` to also return items in the Trash.
        Callers that only want active items should filter on
        ``status == Status.OPEN`` themselves — this method doesn't.
        """
        return self._tasks(_TYPE_TODO, include_trashed, _todo_from_row)

    def projects(self, *, include_trashed: bool = False) -> list[Project]:
        """Every non-trashed project (type=1), any status."""
        return self._tasks(_TYPE_PROJECT, include_trashed, _project_from_row)

    def areas(self) -> list[Area]:
        with closing(self._connect()) as con:
            rows = con.execute(
                'SELECT uuid, title FROM TMArea ORDER BY "index"'
            ).fetchall()
            tags_by_owner = _tags_by_owner(con, "TMAreaTag", "areas")
        return [
            Area(
                id=r["uuid"],
                name=r["title"] or "",
                tag_names=tags_by_owner.get(r["uuid"], ()),
                # TMArea doesn't persist the sidebar collapsed state in
                # the schema we know about; surface the default instead
                # of fabricating an unreliable value.
                collapsed=False,
            )
            for r in rows
        ]

    def tags(self) -> list[Tag]:
        with closing(self._connect()) as con:
            rows = con.execute(
                'SELECT uuid, title, shortcut, parent FROM TMTag ORDER BY "index"'
            ).fetchall()
        return [
            Tag(
                id=r["uuid"],
                name=r["title"] or "",
                parent_id=r["parent"] or None,
                keyboard_shortcut=r["shortcut"] or "",
            )
            for r in rows
        ]

    # ---------------------------------------------------------- task helpers

    def _tasks(self, type_: int, include_trashed: bool, parse) -> list:
        sql = """
            SELECT uuid, title, notes, status, trashed,
                   creationDate, userModificationDate, stopDate,
                   start, startDate, deadline,
                   project, area, contact
            FROM TMTask
            WHERE type = ?
        """
        if not include_trashed:
            sql += " AND trashed = 0"
        sql += ' ORDER BY "index"'
        with closing(self._connect()) as con:
            rows = con.execute(sql, (type_,)).fetchall()
            tags_by_task = _tags_by_owner(con, "TMTaskTag", "tasks")
        return [parse(r, tags_by_task) for r in rows]


def _tags_by_owner(
    con: sqlite3.Connection, table: str, owner_col: str
) -> dict[str, tuple[str, ...]]:
    """Resolve the many-to-many tag join for either ``TMTaskTag`` or
    ``TMAreaTag`` in one query, returning ``{owner_uuid: (name, ...)}``.
    """
    rows = con.execute(
        f"""
        SELECT j.{owner_col} AS owner, TMTag.title AS name
        FROM {table} AS j
        JOIN TMTag ON TMTag.uuid = j.tags
        """
    ).fetchall()
    out: dict[str, list[str]] = {}
    for r in rows:
        if r["name"]:
            out.setdefault(r["owner"], []).append(r["name"])
    return {k: tuple(v) for k, v in out.items()}


def _todo_from_row(r: sqlite3.Row, tags_by_task: dict[str, tuple[str, ...]]) -> Todo:
    status = _STATUS_BY_INT.get(r["status"], Status.OPEN)
    stop = _from_unix(r["stopDate"])
    return Todo(
        id=r["uuid"],
        name=r["title"] or "",
        notes=r["notes"] or "",
        status=status,
        due_date=_decode_packed_date(r["deadline"]),
        activation_date=_decode_packed_date(r["startDate"]),
        completion_date=stop if r["status"] == _STATUS_COMPLETED else None,
        cancellation_date=stop if r["status"] == _STATUS_CANCELED else None,
        creation_date=_from_unix(r["creationDate"]),
        modification_date=_from_unix(r["userModificationDate"]),
        tag_names=tags_by_task.get(r["uuid"], ()),
        project_id=r["project"] or None,
        area_id=r["area"] or None,
        contact_id=r["contact"] or None,
        start_bucket=_start_bucket(r["start"]),
    )


def _project_from_row(r: sqlite3.Row, tags_by_task: dict[str, tuple[str, ...]]) -> Project:
    status = _STATUS_BY_INT.get(r["status"], Status.OPEN)
    stop = _from_unix(r["stopDate"])
    return Project(
        id=r["uuid"],
        name=r["title"] or "",
        notes=r["notes"] or "",
        status=status,
        due_date=_decode_packed_date(r["deadline"]),
        activation_date=_decode_packed_date(r["startDate"]),
        completion_date=stop if r["status"] == _STATUS_COMPLETED else None,
        cancellation_date=stop if r["status"] == _STATUS_CANCELED else None,
        creation_date=_from_unix(r["creationDate"]),
        modification_date=_from_unix(r["userModificationDate"]),
        tag_names=tags_by_task.get(r["uuid"], ()),
        area_id=r["area"] or None,
    )
