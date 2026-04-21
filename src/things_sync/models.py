from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Status(str, Enum):
    OPEN = "open"
    COMPLETED = "completed"
    CANCELED = "canceled"


@dataclass(frozen=True)
class Todo:
    id: str
    name: str
    notes: str = ""
    status: Status = Status.OPEN
    due_date: datetime | None = None
    activation_date: datetime | None = None
    completion_date: datetime | None = None
    cancellation_date: datetime | None = None
    creation_date: datetime | None = None
    modification_date: datetime | None = None
    tag_names: tuple[str, ...] = ()
    project_id: str | None = None
    area_id: str | None = None
    contact_id: str | None = None


@dataclass(frozen=True)
class Project:
    id: str
    name: str
    notes: str = ""
    status: Status = Status.OPEN
    due_date: datetime | None = None
    activation_date: datetime | None = None
    completion_date: datetime | None = None
    cancellation_date: datetime | None = None
    creation_date: datetime | None = None
    modification_date: datetime | None = None
    tag_names: tuple[str, ...] = ()
    area_id: str | None = None


@dataclass(frozen=True)
class Area:
    id: str
    name: str
    tag_names: tuple[str, ...] = ()
    collapsed: bool = False


@dataclass(frozen=True)
class Tag:
    id: str
    name: str
    parent_id: str | None = None
    keyboard_shortcut: str = ""


@dataclass(frozen=True)
class Contact:
    id: str
    name: str


@dataclass(frozen=True)
class ListInfo:
    id: str
    name: str
