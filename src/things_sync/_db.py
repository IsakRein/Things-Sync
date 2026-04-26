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

from .models import Area, Contact, Heading, Project, StartBucket, Status, Tag, Todo


_DB_GLOB = (
    "Library/Group Containers/JLMPQHK86H.com.culturedcode.ThingsMac/"
    "ThingsData-*/Things Database.thingsdatabase/main.sqlite"
)

# TMTask.type
_TYPE_TODO = 0
_TYPE_PROJECT = 1
_TYPE_HEADING = 2

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


def _encode_packed_date(d) -> int:
    """Inverse of :func:`_decode_packed_date`. Accepts a date or datetime."""
    return (d.year << 16) | (d.month << 12) | (d.day << 7)


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

    def headings(self, *, include_trashed: bool = False) -> list[Heading]:
        """Every non-trashed heading (type=2), any status.

        A heading lives inside a project; its ``project_id`` is the parent
        project's UUID. Todos under a heading reference it via their
        ``heading`` column (exposed on :class:`Todo` as ``heading_id``).
        """
        sql = """
            SELECT uuid, title, status, project
            FROM TMTask
            WHERE type = ?
        """
        if not include_trashed:
            sql += " AND trashed = 0"
        sql += ' ORDER BY "index"'
        with closing(self._connect()) as con:
            rows = con.execute(sql, (_TYPE_HEADING,)).fetchall()
        return [
            Heading(
                id=r["uuid"],
                name=r["title"] or "",
                project_id=r["project"] or None,
                status=_STATUS_BY_INT.get(r["status"], Status.OPEN),
            )
            for r in rows
        ]

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

    def contacts(self) -> list[Contact]:
        with closing(self._connect()) as con:
            rows = con.execute(
                'SELECT uuid, displayName FROM TMContact ORDER BY "index"'
            ).fetchall()
        return [Contact(id=r["uuid"], name=r["displayName"] or "") for r in rows]

    # --------------------------------------------------------- by-id lookups

    def todo(self, id: str, *, include_trashed: bool = True) -> Todo | None:
        return self._task_by_id(id, _TYPE_TODO, include_trashed, _todo_from_row)

    def project(self, id: str, *, include_trashed: bool = True) -> Project | None:
        return self._task_by_id(id, _TYPE_PROJECT, include_trashed, _project_from_row)

    def heading(self, id: str, *, include_trashed: bool = True) -> Heading | None:
        sql = "SELECT uuid, title, status, project FROM TMTask WHERE uuid=? AND type=?"
        if not include_trashed:
            sql += " AND trashed=0"
        with closing(self._connect()) as con:
            r = con.execute(sql, (id, _TYPE_HEADING)).fetchone()
        if r is None:
            return None
        return Heading(
            id=r["uuid"],
            name=r["title"] or "",
            project_id=r["project"] or None,
            status=_STATUS_BY_INT.get(r["status"], Status.OPEN),
        )

    def area(self, id: str) -> Area | None:
        with closing(self._connect()) as con:
            r = con.execute(
                "SELECT uuid, title FROM TMArea WHERE uuid=?", (id,)
            ).fetchone()
            tags_by_owner = _tags_by_owner(con, "TMAreaTag", "areas")
        if r is None:
            return None
        return Area(
            id=r["uuid"],
            name=r["title"] or "",
            tag_names=tags_by_owner.get(r["uuid"], ()),
            collapsed=False,
        )

    def tag(self, name: str) -> Tag | None:
        """Look up by display name (Things tags are name-unique)."""
        with closing(self._connect()) as con:
            r = con.execute(
                'SELECT uuid, title, shortcut, parent FROM TMTag WHERE title=?', (name,)
            ).fetchone()
        if r is None:
            return None
        return Tag(
            id=r["uuid"],
            name=r["title"] or "",
            parent_id=r["parent"] or None,
            keyboard_shortcut=r["shortcut"] or "",
        )

    def tag_by_id(self, id: str) -> Tag | None:
        with closing(self._connect()) as con:
            r = con.execute(
                'SELECT uuid, title, shortcut, parent FROM TMTag WHERE uuid=?', (id,)
            ).fetchone()
        if r is None:
            return None
        return Tag(
            id=r["uuid"],
            name=r["title"] or "",
            parent_id=r["parent"] or None,
            keyboard_shortcut=r["shortcut"] or "",
        )

    # ------------------------------------------------------- filtered todos

    def todos_in_project(self, project_id: str, *, include_trashed: bool = False) -> list[Todo]:
        return self._tasks_filtered(_TYPE_TODO, "project=?", (project_id,), include_trashed)

    def todos_in_area(self, area_id: str, *, include_trashed: bool = False) -> list[Todo]:
        return self._tasks_filtered(_TYPE_TODO, "area=?", (area_id,), include_trashed)

    def todos_under_heading(self, heading_id: str, *, include_trashed: bool = False) -> list[Todo]:
        return self._tasks_filtered(_TYPE_TODO, "heading=?", (heading_id,), include_trashed)

    def todos_in_list(self, name: str) -> list[Todo]:
        """Built-in virtual list — Things derives these from TMTask columns
        rather than storing them. The AppleScript ``to dos of list "X"``
        path is broken in Things 3.22.11 (returns -1728), so we
        re-implement the derivation against SQLite.

        Supported names (case-insensitive): Inbox, Today, Upcoming,
        Anytime, Someday, Logbook, Trash. Heuristic but matches the UI
        for the cases atlas cares about.
        """
        from datetime import date as _date

        n = name.lower()
        today = _encode_packed_date(_date.today())

        # base SELECT shared by all branches
        cols = (
            'uuid, title, notes, status, trashed, '
            'creationDate, userModificationDate, stopDate, '
            'start, startDate, deadline, '
            'project, area, contact, heading'
        )
        if n == "trash":
            sql = f'SELECT {cols} FROM TMTask WHERE trashed=1 AND type=? ORDER BY "index"'
            params: tuple = (_TYPE_TODO,)
        elif n == "logbook":
            sql = (
                f'SELECT {cols} FROM TMTask '
                'WHERE type=? AND trashed=0 AND status IN (2, 3) ORDER BY stopDate DESC'
            )
            params = (_TYPE_TODO,)
        elif n == "inbox":
            sql = (
                f'SELECT {cols} FROM TMTask '
                'WHERE type=? AND trashed=0 AND status=0 AND start=0 ORDER BY "index"'
            )
            params = (_TYPE_TODO,)
        elif n == "today":
            # Today = open + scheduled-and-arrived. Includes pinned via
            # todayIndexReferenceDate but leaving that for atlas to refine.
            sql = (
                f'SELECT {cols} FROM TMTask '
                'WHERE type=? AND trashed=0 AND status=0 '
                'AND startDate IS NOT NULL AND startDate <= ? '
                'ORDER BY todayIndex'
            )
            params = (_TYPE_TODO, today)
        elif n == "upcoming":
            sql = (
                f'SELECT {cols} FROM TMTask '
                'WHERE type=? AND trashed=0 AND status=0 '
                'AND startDate IS NOT NULL AND startDate > ? '
                'ORDER BY startDate, "index"'
            )
            params = (_TYPE_TODO, today)
        elif n == "anytime":
            # Active items in the Anytime bucket without a future schedule.
            sql = (
                f'SELECT {cols} FROM TMTask '
                'WHERE type=? AND trashed=0 AND status=0 AND start=1 '
                'AND (startDate IS NULL OR startDate <= ?) ORDER BY "index"'
            )
            params = (_TYPE_TODO, today)
        elif n == "someday":
            sql = (
                f'SELECT {cols} FROM TMTask '
                'WHERE type=? AND trashed=0 AND status=0 AND start=2 ORDER BY "index"'
            )
            params = (_TYPE_TODO,)
        else:
            raise ValueError(
                f"unknown built-in list {name!r}; expected one of "
                "Inbox, Today, Upcoming, Anytime, Someday, Logbook, Trash"
            )

        with closing(self._connect()) as con:
            rows = con.execute(sql, params).fetchall()
            tags_by_task = _tags_by_owner(con, "TMTaskTag", "tasks")
        return [_todo_from_row(r, tags_by_task) for r in rows]

    def todos_with_tag(self, name: str, *, include_trashed: bool = False) -> list[Todo]:
        sql = """
            SELECT TMTask.uuid AS uuid, title, notes, status, trashed,
                   creationDate, userModificationDate, stopDate,
                   start, startDate, deadline,
                   project, area, contact, heading
            FROM TMTask
            JOIN TMTaskTag ON TMTaskTag.tasks = TMTask.uuid
            JOIN TMTag ON TMTag.uuid = TMTaskTag.tags
            WHERE TMTask.type = ? AND TMTag.title = ?
        """
        if not include_trashed:
            sql += " AND TMTask.trashed = 0"
        sql += ' ORDER BY TMTask."index"'
        with closing(self._connect()) as con:
            rows = con.execute(sql, (_TYPE_TODO, name)).fetchall()
            tags_by_task = _tags_by_owner(con, "TMTaskTag", "tasks")
        return [_todo_from_row(r, tags_by_task) for r in rows]

    # --------------------------------------------------------- counts / exists

    def count_todos(self, *, include_trashed: bool = False) -> int:
        return self._count(_TYPE_TODO, include_trashed)

    def count_projects(self, *, include_trashed: bool = False) -> int:
        return self._count(_TYPE_PROJECT, include_trashed)

    def count_areas(self) -> int:
        with closing(self._connect()) as con:
            return con.execute("SELECT COUNT(*) FROM TMArea").fetchone()[0]

    def count_tags(self) -> int:
        with closing(self._connect()) as con:
            return con.execute("SELECT COUNT(*) FROM TMTag").fetchone()[0]

    def exists(self, id: str) -> bool:
        with closing(self._connect()) as con:
            for sql, params in (
                ("SELECT 1 FROM TMTask WHERE uuid=? LIMIT 1", (id,)),
                ("SELECT 1 FROM TMArea WHERE uuid=? LIMIT 1", (id,)),
                ("SELECT 1 FROM TMTag WHERE uuid=? LIMIT 1", (id,)),
            ):
                if con.execute(sql, params).fetchone() is not None:
                    return True
        return False

    # ---------------------------------------------------------- task helpers

    def _task_by_id(self, id: str, type_: int, include_trashed: bool, parse):
        sql = """
            SELECT uuid, title, notes, status, trashed,
                   creationDate, userModificationDate, stopDate,
                   start, startDate, deadline,
                   project, area, contact, heading
            FROM TMTask
            WHERE uuid=? AND type=?
        """
        if not include_trashed:
            sql += " AND trashed=0"
        with closing(self._connect()) as con:
            row = con.execute(sql, (id, type_)).fetchone()
            if row is None:
                return None
            tags_by_task = _tags_by_owner(con, "TMTaskTag", "tasks")
        return parse(row, tags_by_task)

    def _tasks_filtered(self, type_: int, where: str, params: tuple, include_trashed: bool) -> list:
        sql = f"""
            SELECT uuid, title, notes, status, trashed,
                   creationDate, userModificationDate, stopDate,
                   start, startDate, deadline,
                   project, area, contact, heading
            FROM TMTask
            WHERE type = ? AND {where}
        """
        if not include_trashed:
            sql += " AND trashed = 0"
        sql += ' ORDER BY "index"'
        with closing(self._connect()) as con:
            rows = con.execute(sql, (type_, *params)).fetchall()
            tags_by_task = _tags_by_owner(con, "TMTaskTag", "tasks")
        return [_todo_from_row(r, tags_by_task) for r in rows]

    def _count(self, type_: int, include_trashed: bool) -> int:
        sql = "SELECT COUNT(*) FROM TMTask WHERE type = ?"
        if not include_trashed:
            sql += " AND trashed = 0"
        with closing(self._connect()) as con:
            return con.execute(sql, (type_,)).fetchone()[0]

    def _tasks(self, type_: int, include_trashed: bool, parse) -> list:
        sql = """
            SELECT uuid, title, notes, status, trashed,
                   creationDate, userModificationDate, stopDate,
                   start, startDate, deadline,
                   project, area, contact, heading
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
        heading_id=r["heading"] or None,
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
