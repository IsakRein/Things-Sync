"""Read-only SQLite access to the Things 3 database.

The database lives at
    ~/Library/Group Containers/JLMPQHK86H.com.culturedcode.ThingsMac/
        ThingsData-*/Things Database.thingsdatabase/main.sqlite

The `ThingsData-*` suffix rotates between installs, so we glob for it.
The connection is opened read-only (`mode=ro`) via a URI so a running
Things.app and live CloudKit sync can't be disturbed by accident.

Env override: `THINGS_DB` — absolute path to `main.sqlite`.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterator

from .models import (
    STATUS_COMPLETED,
    STATUS_OPEN,
    START_ANYTIME,
    START_INBOX,
    START_SOMEDAY,
    TYPE_HEADING,
    TYPE_PROJECT,
    TYPE_TASK,
    Area,
    Project,
    Tag,
    Task,
    decode_packed_date,
)

GROUP_CONTAINER = Path.home() / "Library/Group Containers/JLMPQHK86H.com.culturedcode.ThingsMac"


def _discover_default_db() -> Path | None:
    override = os.environ.get("THINGS_DB")
    if override:
        return Path(override).expanduser()
    if not GROUP_CONTAINER.exists():
        return None
    matches = sorted(GROUP_CONTAINER.glob("ThingsData-*/Things Database.thingsdatabase/main.sqlite"))
    return matches[0] if matches else None


DEFAULT_DB_PATH = _discover_default_db()


# Columns we always want off TMTask. Kept in one place so all queries
# hydrate the same shape and `Task.from_row` stays simple.
TASK_COLUMNS = """
    uuid, title, notes, type, status, start, startDate, deadline,
    creationDate, userModificationDate, stopDate,
    area, project, heading,
    openChecklistItemsCount, checklistItemsCount
""".strip()


def _pack_date(d: date) -> int:
    return (d.year << 16) | (d.month << 12) | (d.day << 7)


class ThingsDB:
    """Thin read-only wrapper over the Things 3 sqlite file.

    Use as a context manager. Queries return model dataclasses, not raw rows.
    """

    def __init__(self, path: Path | None = None) -> None:
        resolved = path or DEFAULT_DB_PATH
        if resolved is None:
            raise FileNotFoundError(
                "Could not locate Things 3 database. Is Things 3 installed? "
                "Set THINGS_DB to override."
            )
        if not resolved.exists():
            raise FileNotFoundError(f"Things database not found: {resolved}")
        self.path = resolved
        self._conn: sqlite3.Connection | None = None

    def __enter__(self) -> "ThingsDB":
        uri = f"file:{self.path}?mode=ro&immutable=0"
        self._conn = sqlite3.connect(uri, uri=True)
        self._conn.row_factory = sqlite3.Row
        return self

    def __exit__(self, *_exc) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("ThingsDB must be used as a context manager")
        return self._conn

    # ---------- task queries ----------

    def _query_tasks(self, where: str, params: tuple = (), *, order: str = "") -> list[Task]:
        sql = f"SELECT {TASK_COLUMNS} FROM TMTask WHERE trashed=0 AND {where}"
        if order:
            sql += f" ORDER BY {order}"
        tasks = [Task.from_row(r) for r in self.conn.execute(sql, params)]
        self._hydrate_tags(tasks)
        return tasks

    def _hydrate_tags(self, tasks: list[Task]) -> None:
        if not tasks:
            return
        index = {t.uuid: t for t in tasks}
        placeholders = ",".join("?" * len(index))
        rows = self.conn.execute(
            f"""
            SELECT tt.tasks AS task_uuid, tag.title AS title
            FROM TMTaskTag tt
            JOIN TMTag tag ON tag.uuid = tt.tags
            WHERE tt.tasks IN ({placeholders})
            """,
            tuple(index.keys()),
        )
        for row in rows:
            index[row["task_uuid"]].tags.append(row["title"])

    def inbox(self) -> list[Task]:
        return self._query_tasks(
            "status=? AND start=? AND type=?",
            (STATUS_OPEN, START_INBOX, TYPE_TASK),
            order='"index" ASC',
        )

    def today(self) -> list[Task]:
        """Tasks whose start_date is today or earlier AND in the Anytime list."""
        today_packed = _pack_date(date.today())
        return self._query_tasks(
            "status=? AND start=? AND type!=? AND startDate IS NOT NULL AND startDate<=?",
            (STATUS_OPEN, START_ANYTIME, TYPE_HEADING, today_packed),
            order='todayIndex ASC, "index" ASC',
        )

    def upcoming(self, days: int = 30) -> list[Task]:
        today_packed = _pack_date(date.today())
        end_packed = _pack_date(date.today() + timedelta(days=days))
        return self._query_tasks(
            "status=? AND type!=? AND startDate IS NOT NULL AND startDate>? AND startDate<=?",
            (STATUS_OPEN, TYPE_HEADING, today_packed, end_packed),
            order="startDate ASC",
        )

    def anytime(self) -> list[Task]:
        today_packed = _pack_date(date.today())
        return self._query_tasks(
            "status=? AND start=? AND type=? AND (startDate IS NULL OR startDate<=?)",
            (STATUS_OPEN, START_ANYTIME, TYPE_TASK, today_packed),
            order='"index" ASC',
        )

    def someday(self) -> list[Task]:
        return self._query_tasks(
            "status=? AND start=? AND type=?",
            (STATUS_OPEN, START_SOMEDAY, TYPE_TASK),
            order='"index" ASC',
        )

    def logbook(self, limit: int = 50) -> list[Task]:
        return self._query_tasks(
            "status IN (?,?) AND type=?",
            (STATUS_COMPLETED, 2, TYPE_TASK),
            order=f"stopDate DESC LIMIT {int(limit)}",
        )

    def search(self, query: str, *, include_completed: bool = False, limit: int = 50) -> list[Task]:
        like = f"%{query}%"
        status_clause = "" if include_completed else f" AND status={STATUS_OPEN}"
        return self._query_tasks(
            f"(title LIKE ? OR notes LIKE ?) AND type!=?{status_clause}",
            (like, like, TYPE_HEADING),
            order=f"userModificationDate DESC LIMIT {int(limit)}",
        )

    def get(self, uuid: str) -> Task | None:
        tasks = self._query_tasks("uuid=?", (uuid,))
        return tasks[0] if tasks else None

    def tasks_for_project(self, project_uuid: str, *, include_completed: bool = False) -> list[Task]:
        status_clause = "" if include_completed else f" AND status={STATUS_OPEN}"
        return self._query_tasks(
            f"project=? AND type=?{status_clause}",
            (project_uuid, TYPE_TASK),
            order='"index" ASC',
        )

    # ---------- project / area / tag queries ----------

    def projects(self, *, include_completed: bool = False) -> list[Project]:
        status_clause = "" if include_completed else f" AND status={STATUS_OPEN}"
        rows = self.conn.execute(
            f"""
            SELECT uuid, title, notes, area, status,
                   openUntrashedLeafActionsCount, untrashedLeafActionsCount,
                   deadline, startDate
            FROM TMTask
            WHERE trashed=0 AND type={TYPE_PROJECT}{status_clause}
            ORDER BY "index" ASC
            """
        )
        return [
            Project(
                uuid=r["uuid"],
                title=r["title"] or "",
                notes=r["notes"] or "",
                area=r["area"],
                status=r["status"] or 0,
                open_actions=r["openUntrashedLeafActionsCount"] or 0,
                total_actions=r["untrashedLeafActionsCount"] or 0,
                deadline=decode_packed_date(r["deadline"]),
                start_date=decode_packed_date(r["startDate"]),
            )
            for r in rows
        ]

    def areas(self, *, visible_only: bool = False) -> list[Area]:
        clause = "WHERE visible=1" if visible_only else ""
        rows = self.conn.execute(
            f'SELECT uuid, title, visible FROM TMArea {clause} ORDER BY "index" ASC'
        )
        return [Area(uuid=r["uuid"], title=r["title"] or "", visible=bool(r["visible"])) for r in rows]

    def tags(self) -> list[Tag]:
        rows = self.conn.execute(
            'SELECT uuid, title, parent, shortcut FROM TMTag ORDER BY "index" ASC'
        )
        return [
            Tag(uuid=r["uuid"], title=r["title"] or "", parent=r["parent"], shortcut=r["shortcut"])
            for r in rows
        ]

    def area_titles(self) -> dict[str, str]:
        return {a.uuid: a.title for a in self.areas()}

    def project_titles(self) -> dict[str, str]:
        rows = self.conn.execute(
            f"SELECT uuid, title FROM TMTask WHERE type={TYPE_PROJECT} AND trashed=0"
        )
        return {r["uuid"]: (r["title"] or "") for r in rows}

    # ---------- stats ----------

    def summary(self) -> dict[str, int]:
        c = self.conn
        inbox = c.execute(
            f"SELECT COUNT(*) FROM TMTask WHERE trashed=0 AND status={STATUS_OPEN} "
            f"AND start={START_INBOX} AND type={TYPE_TASK}"
        ).fetchone()[0]
        anytime = c.execute(
            f"SELECT COUNT(*) FROM TMTask WHERE trashed=0 AND status={STATUS_OPEN} "
            f"AND start={START_ANYTIME} AND type={TYPE_TASK}"
        ).fetchone()[0]
        someday = c.execute(
            f"SELECT COUNT(*) FROM TMTask WHERE trashed=0 AND status={STATUS_OPEN} "
            f"AND start={START_SOMEDAY} AND type={TYPE_TASK}"
        ).fetchone()[0]
        projects = c.execute(
            f"SELECT COUNT(*) FROM TMTask WHERE trashed=0 AND status={STATUS_OPEN} "
            f"AND type={TYPE_PROJECT}"
        ).fetchone()[0]
        today_packed = _pack_date(date.today())
        today = c.execute(
            f"SELECT COUNT(*) FROM TMTask WHERE trashed=0 AND status={STATUS_OPEN} "
            f"AND start={START_ANYTIME} AND type={TYPE_TASK} "
            f"AND startDate IS NOT NULL AND startDate<=?",
            (today_packed,),
        ).fetchone()[0]
        return {
            "inbox": inbox,
            "today": today,
            "anytime": anytime,
            "someday": someday,
            "projects": projects,
        }


@contextmanager
def open_db(path: Path | None = None) -> Iterator[ThingsDB]:
    with ThingsDB(path) as db:
        yield db
