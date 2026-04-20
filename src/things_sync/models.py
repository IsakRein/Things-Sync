"""Dataclasses + codec helpers for Things 3 rows.

Things 3 stores dates in two formats:

    - `REAL` Unix timestamp, seconds since 1970-01-01 UTC
      (e.g. creationDate, userModificationDate, stopDate — same as the cloud API)
    - `INTEGER` packed YYYYMMDD: year<<16 | month<<12 | day<<7
      (e.g. startDate, deadline — the user-visible "Today/Evening" date)

Hidden behind `decode_unix_ts` and `decode_packed_date`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone


def decode_unix_ts(value: float | int | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(float(value), tz=timezone.utc).astimezone()


def decode_packed_date(value: int | None) -> date | None:
    if not value:
        return None
    year = value >> 16
    month = (value >> 12) & 0xF
    day = (value >> 7) & 0x1F
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    return date(year, month, day)


# TMTask.type
TYPE_TASK = 0
TYPE_PROJECT = 1
TYPE_HEADING = 2

# TMTask.status
STATUS_OPEN = 0
STATUS_CANCELED = 2
STATUS_COMPLETED = 3

# TMTask.start
START_INBOX = 0
START_ANYTIME = 1
START_SOMEDAY = 2

START_LABEL = {0: "inbox", 1: "anytime", 2: "someday"}
STATUS_LABEL = {0: "open", 2: "canceled", 3: "completed"}
TYPE_LABEL = {0: "task", 1: "project", 2: "heading"}


@dataclass
class Task:
    uuid: str
    title: str
    notes: str = ""
    type: int = TYPE_TASK
    status: int = STATUS_OPEN
    start: int = START_INBOX
    start_date: date | None = None
    deadline: date | None = None
    creation_date: datetime | None = None
    modification_date: datetime | None = None
    stop_date: datetime | None = None
    area: str | None = None
    project: str | None = None
    heading: str | None = None
    tags: list[str] = field(default_factory=list)
    checklist_open: int = 0
    checklist_total: int = 0

    @classmethod
    def from_row(cls, row) -> "Task":
        return cls(
            uuid=row["uuid"],
            title=row["title"] or "",
            notes=row["notes"] or "",
            type=row["type"] or 0,
            status=row["status"] or 0,
            start=row["start"] or 0,
            start_date=decode_packed_date(row["startDate"]),
            deadline=decode_packed_date(row["deadline"]),
            creation_date=decode_unix_ts(row["creationDate"]),
            modification_date=decode_unix_ts(row["userModificationDate"]),
            stop_date=decode_unix_ts(row["stopDate"]),
            area=row["area"],
            project=row["project"],
            heading=row["heading"],
            checklist_open=row["openChecklistItemsCount"] or 0,
            checklist_total=row["checklistItemsCount"] or 0,
        )

    @property
    def is_project(self) -> bool:
        return self.type == TYPE_PROJECT

    @property
    def is_heading(self) -> bool:
        return self.type == TYPE_HEADING

    @property
    def status_label(self) -> str:
        return STATUS_LABEL.get(self.status, str(self.status))

    @property
    def start_label(self) -> str:
        return START_LABEL.get(self.start, str(self.start))


@dataclass
class Project:
    uuid: str
    title: str
    area: str | None = None
    notes: str = ""
    status: int = STATUS_OPEN
    open_actions: int = 0
    total_actions: int = 0
    deadline: date | None = None
    start_date: date | None = None


@dataclass
class Area:
    uuid: str
    title: str
    visible: bool = True


@dataclass
class Tag:
    uuid: str
    title: str
    parent: str | None = None
    shortcut: str | None = None
