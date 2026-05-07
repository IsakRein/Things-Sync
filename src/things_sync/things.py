"""Public Python API: a single ``Things`` class.

Two layers stacked underneath:

- **Writes** → AppleScript via ``osascript``. Synchronous against Things'
  local store: a write returns once Things has committed it to TMTask,
  so a follow-up :class:`ThingsDB` read sees it immediately.
- **Reads** → :class:`ThingsDB` (read-only SQLite at disk speed).

Things 3 must be installed and running on the local Mac. There is no
networked / cloud-direct write path.
"""
from __future__ import annotations

import subprocess
import tempfile
import time
from contextlib import closing
from dataclasses import replace
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Iterable

from . import _osascript as osa
from ._db import ThingsDB
from ._osascript import US, as_date, as_str, parse_iso, parse_records
from ._scripts import script
from .models import Area, Contact, Heading, ListInfo, Project, Status, Tag, Todo

TELL = 'application id "com.culturedcode.ThingsMac"'
BUILTIN_LISTS = ("Inbox", "Today", "Anytime", "Upcoming", "Someday", "Logbook", "Trash")
SHORTCUT_ADD_HEADING = "Things-Sync Add Heading"

_STATUS_AS = {
    Status.OPEN: "open",
    Status.COMPLETED: "completed",
    Status.CANCELED: "canceled",
}


class _Sentinel(Enum):
    UNSET = object()


UNSET = _Sentinel.UNSET


def _csv_tags(tags: Iterable[str] | None) -> str:
    return ",".join(tags) if tags else ""


def _split_tags(s: str) -> tuple[str, ...]:
    if not s:
        return ()
    return tuple(t.strip() for t in s.split(",") if t.strip())


def _to_dt(v: date | datetime | str | None) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, str):
        v = date.fromisoformat(v)
    if isinstance(v, datetime):
        return v
    return datetime(v.year, v.month, v.day)


class Things:
    """Façade over Things 3. Writes via AppleScript; reads via SQLite."""

    def __init__(self) -> None:
        self._db: ThingsDB | None = None

    @property
    def db(self) -> ThingsDB:
        if self._db is None:
            self._db = ThingsDB()
        return self._db

    # ------------------------------------------------------------------ meta

    def version(self) -> str:
        return osa.run(f'tell {TELL} to return version as text')

    def is_running(self) -> bool:
        out = osa.run(
            'tell application "System Events" to '
            'return (exists (processes whose bundle identifier is "com.culturedcode.ThingsMac")) as text'
        )
        return out == "true"

    def quit(self) -> None:
        osa.run(f"tell {TELL} to quit")

    def launch(self) -> None:
        """``tell ... to activate``. Brings Things to foreground."""
        osa.run(f"tell {TELL} to activate")

    # --------------------------------------------------------- bulk reads (DB)

    def todos(self) -> list[Todo]:
        return self.db.todos()

    def projects(self) -> list[Project]:
        return self.db.projects()

    def areas(self) -> list[Area]:
        return self.db.areas()

    def tags(self) -> list[Tag]:
        return self.db.tags()

    def contacts(self) -> list[Contact]:
        return self.db.contacts()

    def headings(self) -> list[Heading]:
        return self.db.headings()

    def lists(self) -> list[ListInfo]:
        """Built-in lists. Returned by name since the AS ``lists``
        collection is broken on Things 3.22.11."""
        return [ListInfo(id=name, name=name) for name in BUILTIN_LISTS]

    # ------------------------------------------------------- by-id reads (DB)

    def todo(self, id: str) -> Todo | None:
        return self.db.todo(id, include_trashed=True)

    def project(self, id: str) -> Project | None:
        return self.db.project(id, include_trashed=True)

    def area(self, id: str) -> Area | None:
        return self.db.area(id)

    def tag(self, name: str) -> Tag | None:
        return self.db.tag(name)

    # -------------------------------------------------------- filtered reads

    def todos_in_project(self, id: str) -> list[Todo]:
        return self.db.todos_in_project(id)

    def todos_in_area(self, id: str) -> list[Todo]:
        return self.db.todos_in_area(id)

    def todos_with_tag(self, name: str) -> list[Todo]:
        return self.db.todos_with_tag(name)

    def todos_in_list(self, name: str) -> list[Todo]:
        """Items in a built-in list (Inbox/Today/Upcoming/Anytime/Someday/
        Logbook/Trash). Derived from TMTask columns since the AS list
        path is broken on Things 3.22.11."""
        return self.db.todos_in_list(name)

    def selected_todos(self) -> list[Todo]:
        body = f"""
        tell {TELL}
            set out to ""
            repeat with t in selected to dos
                if out is "" then
                    set out to my serializeTodo(t)
                else
                    set out to out & RS & my serializeTodo(t)
                end if
            end repeat
            return out
        end tell
        """
        return [_parse_todo(r) for r in parse_records(osa.run(script(body)))]

    # ------------------------------------------------------- counts / exists

    def count_todos(self) -> int:
        return self.db.count_todos()

    def count_projects(self) -> int:
        return self.db.count_projects()

    def count_areas(self) -> int:
        return self.db.count_areas()

    def count_tags(self) -> int:
        return self.db.count_tags()

    def exists(self, id: str) -> bool:
        return self.db.exists(id)

    # --------------------------------------------------------------- creates

    def create_todo(
        self,
        name: str,
        *,
        notes: str | None = None,
        when: date | datetime | str | None = None,
        deadline: date | datetime | str | None = None,
        tags: Iterable[str] | None = None,
        project: str | None = None,
        area: str | None = None,
        contact: str | None = None,
    ) -> Todo:
        props = [f"name:{as_str(name)}"]
        if notes is not None:
            props.append(f"notes:{as_str(notes)}")
        if deadline is not None:
            props.append(f"due date:{as_date(deadline)}")
        if tags:
            props.append(f"tag names:{as_str(_csv_tags(tags))}")
        record = "{" + ", ".join(props) + "}"
        post: list[str] = []
        if project is not None:
            post.append(f"set project of t to (project id {as_str(project)})")
        elif area is not None:
            post.append(f"set area of t to (area id {as_str(area)})")
        if when is not None:
            post.append(f"schedule t for {as_date(when)}")
        if contact is not None:
            post.append(f"set contact of t to (contact id {as_str(contact)})")
        post_block = "\n            ".join(post)
        body = f"""
        tell {TELL}
            set t to make new to do with properties {record}
            {post_block}
            return my serializeTodo(t)
        end tell
        """
        return _parse_todo(osa.run(script(body)).split(US))

    def create_project(
        self,
        name: str,
        *,
        notes: str | None = None,
        when: date | datetime | str | None = None,
        deadline: date | datetime | str | None = None,
        tags: Iterable[str] | None = None,
        area: str | None = None,
    ) -> Project:
        props = [f"name:{as_str(name)}"]
        if notes is not None:
            props.append(f"notes:{as_str(notes)}")
        if deadline is not None:
            props.append(f"due date:{as_date(deadline)}")
        if tags:
            props.append(f"tag names:{as_str(_csv_tags(tags))}")
        record = "{" + ", ".join(props) + "}"
        post: list[str] = []
        if area is not None:
            post.append(f"set area of p to (area id {as_str(area)})")
        if when is not None:
            post.append(f"schedule p for {as_date(when)}")
        post_block = "\n            ".join(post)
        body = f"""
        tell {TELL}
            set p to make new project with properties {record}
            {post_block}
            return my serializeProject(p)
        end tell
        """
        return _parse_project(osa.run(script(body)).split(US))

    def create_area(
        self,
        name: str,
        *,
        tags: Iterable[str] | None = None,
    ) -> Area:
        props = [f"name:{as_str(name)}"]
        if tags:
            props.append(f"tag names:{as_str(_csv_tags(tags))}")
        record = "{" + ", ".join(props) + "}"
        body = f"""
        tell {TELL}
            set a to make new area with properties {record}
            return my serializeArea(a)
        end tell
        """
        return _parse_area(osa.run(script(body)).split(US))

    def create_tag(
        self,
        name: str,
        *,
        parent: str | None = None,
        shortcut: str | None = None,
    ) -> Tag:
        props = [f"name:{as_str(name)}"]
        if shortcut is not None:
            props.append(f"keyboard shortcut:{as_str(shortcut)}")
        record = "{" + ", ".join(props) + "}"
        post = ""
        if parent is not None:
            post = f"set parent tag of g to tag {as_str(parent)}"
        body = f"""
        tell {TELL}
            set g to make new tag with properties {record}
            {post}
            return my serializeTag(g)
        end tell
        """
        return _parse_tag(osa.run(script(body)).split(US))

    def create_contact(self, name: str) -> Contact:
        body = f"""
        tell {TELL}
            set c to add contact named {as_str(name)}
            return my serializeContact(c)
        end tell
        """
        return _parse_contact(osa.run(script(body)).split(US))

    def create_heading(self, project_id: str, name: str, *, timeout: float = 30.0) -> Heading:
        """Create a heading inside a project via Shortcuts.app.

        Things 3's AppleScript dictionary doesn't expose heading creation
        (``make new heading`` raises -2753). The Shortcuts app, however,
        does — so this routes through a user-configured shortcut.

        One-time setup in Shortcuts.app:
          1. New Shortcut → name it exactly ``Things-Sync Add Heading``.
          2. Receive Shortcut Input as Text.
          3. Split Text by **New Lines** → item 1 = project_id, item 2 = title.
          4. Things 3 → Find Project where ID matches item 1.
          5. Things 3 → Add Heading: title = item 2, Project = the
             found project.
          6. Stop and Output: the new heading's ``ID``.

        Input wire format: project_id, newline, title. Output: the
        new heading's UUID.
        """
        if "\n" in name:
            raise ValueError("heading name cannot contain a newline (used as wire separator)")
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as inp:
            inp.write(f"{project_id}\n{name}")
            in_path = inp.name
        out_path = in_path + ".out"
        try:
            result = subprocess.run(
                [
                    "/usr/bin/shortcuts", "run", SHORTCUT_ADD_HEADING,
                    "-i", in_path, "-o", out_path,
                    "--output-type", "public.plain-text",
                ],
                capture_output=True, text=True, timeout=timeout, check=False,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"`shortcuts run {SHORTCUT_ADD_HEADING}` failed "
                    f"(exit {result.returncode}): {result.stderr.strip() or result.stdout.strip()}. "
                    "Is the shortcut set up? See Things.create_heading docstring."
                )
            uuid = ""
            try:
                uuid = Path(out_path).read_text().strip()
            except FileNotFoundError:
                pass
        finally:
            for p in (in_path, out_path):
                try:
                    Path(p).unlink()
                except FileNotFoundError:
                    pass

        if uuid:
            h = self.db.heading(uuid)
            if h is not None:
                return h
        # Fallback: shortcut didn't surface a usable id — match by name in project.
        matches = [h for h in self.db.headings() if h.project_id == project_id and h.name == name]
        if not matches:
            raise RuntimeError(
                f"create_heading ran but no heading appeared under project {project_id!r} "
                f"with name {name!r} (shortcut output: {uuid!r})"
            )
        return matches[-1]

    def parse_quicksilver(self, text: str) -> Todo:
        """Pop the Quick Entry parser — UI-only feature."""
        body = f"""
        tell {TELL}
            set t to parse quicksilver input {as_str(text)}
            return my serializeTodo(t)
        end tell
        """
        return _parse_todo(osa.run(script(body)).split(US))

    # --------------------------------------------------------------- updates

    def update_todo(
        self,
        id: str,
        *,
        name: str | None = None,
        notes: str | None = None,
        due_date: date | datetime | str | None | _Sentinel = UNSET,  # type: ignore[name-defined]
        tags: Iterable[str] | None = None,
        status: Status | None = None,
        project: str | None | _Sentinel = UNSET,  # type: ignore[name-defined]
        area: str | None | _Sentinel = UNSET,  # type: ignore[name-defined]
        contact: str | None | _Sentinel = UNSET,  # type: ignore[name-defined]
    ) -> Todo:
        if due_date is None:
            raise NotImplementedError(
                "Clearing a due date isn't supported: AppleScript refuses "
                "missing-value for date-typed properties. Clear it from the "
                "Things UI."
            )
        sets: list[str] = []
        if name is not None:
            sets.append(f"set name of t to {as_str(name)}")
        if notes is not None:
            sets.append(f"set notes of t to {as_str(notes)}")
        if due_date is not UNSET:
            sets.append(f"set due date of t to {as_date(due_date)}")
        if tags is not None:
            sets.append(f"set tag names of t to {as_str(_csv_tags(tags))}")
        if status is not None:
            sets.append(f"set status of t to {_STATUS_AS[status]}")
        if project is not UNSET:
            if project is None:
                sets.append('move t to list "Inbox"')
            else:
                sets.append(f"set project of t to (project id {as_str(project)})")
        if area is not UNSET:
            if area is None:
                sets.append('move t to list "Inbox"')
            else:
                sets.append(f"set area of t to (area id {as_str(area)})")
        if contact is not UNSET:
            if contact is None:
                sets.append("set contact of t to missing value")
            else:
                sets.append(f"set contact of t to (contact id {as_str(contact)})")
        if sets:
            sets_block = "\n            ".join(sets)
            body = f"""
            tell {TELL}
                set t to to do id {as_str(id)}
                {sets_block}
            end tell
            """
            osa.run(script(body))
        return self._effective_todo(id, name=name, notes=notes, due_date=due_date,
                                    tags=tags, status=status, project=project,
                                    area=area)

    def update_project(
        self,
        id: str,
        *,
        name: str | None = None,
        notes: str | None = None,
        due_date: date | datetime | str | None | _Sentinel = UNSET,  # type: ignore[name-defined]
        tags: Iterable[str] | None = None,
        status: Status | None = None,
        area: str | None | _Sentinel = UNSET,  # type: ignore[name-defined]
    ) -> Project:
        if due_date is None:
            raise NotImplementedError(
                "Clearing a due date isn't supported: AppleScript refuses "
                "missing-value for date-typed properties. Clear it from the "
                "Things UI."
            )
        sets: list[str] = []
        if name is not None:
            sets.append(f"set name of p to {as_str(name)}")
        if notes is not None:
            sets.append(f"set notes of p to {as_str(notes)}")
        if due_date is not UNSET:
            sets.append(f"set due date of p to {as_date(due_date)}")
        if tags is not None:
            sets.append(f"set tag names of p to {as_str(_csv_tags(tags))}")
        if status is not None:
            sets.append(f"set status of p to {_STATUS_AS[status]}")
        if area is not UNSET:
            if area is None:
                sets.append("set area of p to missing value")
            else:
                sets.append(f"set area of p to (area id {as_str(area)})")
        if sets:
            sets_block = "\n            ".join(sets)
            body = f"""
            tell {TELL}
                set p to project id {as_str(id)}
                {sets_block}
                return my serializeProject(p)
            end tell
            """
            return _parse_project(osa.run(script(body)).split(US))
        # No change — read current.
        base = self.db.project(id, include_trashed=True) or Project(id=id, name=name or "")
        return base

    def update_area(
        self,
        id: str,
        *,
        name: str | None = None,
        tags: Iterable[str] | None = None,
        collapsed: bool | None = None,
    ) -> Area:
        sets: list[str] = []
        if name is not None:
            sets.append(f"set name of a to {as_str(name)}")
        if tags is not None:
            sets.append(f"set tag names of a to {as_str(_csv_tags(tags))}")
        if collapsed is not None:
            sets.append(f"set collapsed of a to {'true' if collapsed else 'false'}")
        sets_block = "\n            ".join(sets)
        body = f"""
        tell {TELL}
            set a to area id {as_str(id)}
            {sets_block}
            return my serializeArea(a)
        end tell
        """
        return _parse_area(osa.run(script(body)).split(US))

    def update_tag(
        self,
        id: str,
        *,
        name: str | None = None,
        shortcut: str | None = None,
        parent: str | None | _Sentinel = UNSET,  # type: ignore[name-defined]
    ) -> Tag:
        sets: list[str] = []
        if name is not None:
            sets.append(f"set name of g to {as_str(name)}")
        if shortcut is not None:
            sets.append(f"set keyboard shortcut of g to {as_str(shortcut)}")
        if parent is not UNSET:
            if parent is None:
                sets.append("set parent tag of g to missing value")
            else:
                sets.append(f"set parent tag of g to tag {as_str(parent)}")
        sets_block = "\n            ".join(sets)
        body = f"""
        tell {TELL}
            set g to tag id {as_str(id)}
            {sets_block}
            return my serializeTag(g)
        end tell
        """
        return _parse_tag(osa.run(script(body)).split(US))

    # ---------------------------------------------------------- status moves

    def complete(self, id: str) -> Todo:
        body = f"""
        tell {TELL}
            set t to to do id {as_str(id)}
            set status of t to completed
        end tell
        """
        osa.run(script(body))
        return self._effective_todo(id, status=Status.COMPLETED, _completed_now=True)

    def cancel(self, id: str) -> Todo:
        body = f"""
        tell {TELL}
            set t to to do id {as_str(id)}
            set status of t to canceled
        end tell
        """
        osa.run(script(body))
        return self._effective_todo(id, status=Status.CANCELED, _canceled_now=True)

    def reopen(self, id: str) -> Todo:
        body = f"""
        tell {TELL}
            set t to to do id {as_str(id)}
            set status of t to open
        end tell
        """
        osa.run(script(body))
        return self._effective_todo(id, status=Status.OPEN, _reopen=True)

    def move_to_list(self, id: str, list_name: str) -> None:
        """Move a todo to a built-in list.

        - Inbox: clear project/area, drop into Inbox
        - Today: schedule for today (Things shows it under Today)
        - Anytime / Someday: route via the AS ``list`` reference
        - Trash: ``delete`` (soft, recoverable until empty_trash)
        - Logbook: complete (Things archives completed items there)

        ``Upcoming`` is derived from a future scheduled date — use
        :meth:`schedule` directly with that date.
        """
        n = list_name.lower()
        if n == "upcoming":
            raise ValueError(
                "Upcoming is derived from a future scheduled date; use "
                "Things.schedule(id, future_date) instead."
            )
        if n == "today":
            body = f"""
            tell {TELL}
                set t to to do id {as_str(id)}
                schedule t for current date
            end tell
            """
        elif n == "trash":
            body = f"""
            tell {TELL}
                delete (to do id {as_str(id)})
            end tell
            """
        elif n == "logbook":
            body = f"""
            tell {TELL}
                set status of (to do id {as_str(id)}) to completed
            end tell
            """
        elif n in ("inbox", "anytime", "someday"):
            target = list_name.capitalize()
            body = f"""
            tell {TELL}
                move (to do id {as_str(id)}) to list {as_str(target)}
            end tell
            """
        else:
            raise ValueError(
                f"unknown list {list_name!r}; expected Inbox/Today/Anytime/"
                "Someday/Logbook/Trash"
            )
        osa.run(script(body))

    def move_to_area(self, id: str, area_id: str) -> None:
        body = f"""
        tell {TELL}
            set t to to do id {as_str(id)}
            set area of t to (area id {as_str(area_id)})
        end tell
        """
        osa.run(script(body))

    def move_to_project(self, id: str, project_id: str) -> None:
        body = f"""
        tell {TELL}
            set t to to do id {as_str(id)}
            set project of t to (project id {as_str(project_id)})
        end tell
        """
        osa.run(script(body))

    def schedule(self, id: str, when: date | datetime | str) -> None:
        body = f"""
        tell {TELL}
            schedule (to do id {as_str(id)}) for {as_date(when)}
        end tell
        """
        osa.run(script(body))

    # -------------------------------------------------------------- deletion

    def delete(self, id: str) -> None:
        """Soft-delete to Trash (recoverable until ``empty_trash``).

        Works for todos, projects, areas, and tags. Things' AS ``delete``
        verb routes the item to the Trash (todos/projects) or removes it
        outright (areas/tags).
        """
        body = f"""
        tell {TELL}
            try
                delete (to do id {as_str(id)})
                return
            end try
            try
                delete (project id {as_str(id)})
                return
            end try
            try
                delete (area id {as_str(id)})
                return
            end try
            try
                delete (tag id {as_str(id)})
                return
            end try
            try
                delete (heading id {as_str(id)})
                return
            end try
        end tell
        """
        osa.run(script(body))

    def empty_trash(self, *, timeout: float = 10.0) -> None:
        """Purge all currently-trashed items.

        AS ``empty trash`` returns before Things has flushed the change
        to its SQLite store, so we poll until no trashed rows remain
        across todos, projects, and headings (or ``timeout`` elapses).
        """
        osa.run(f"tell {TELL} to empty trash")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._count_trashed() == 0:
                return
            time.sleep(0.1)

    def _count_trashed(self) -> int:
        with closing(self.db._connect()) as con:
            (cnt,) = con.execute(
                "SELECT COUNT(*) FROM TMTask WHERE trashed = 1"
            ).fetchone()
        return cnt

    def delete_immediately(self, id: str, *, timeout: float = 10.0) -> None:
        """Soft-trash, then empty Trash, then wait for the row to actually
        leave the local SQLite store."""
        self.delete(id)
        osa.run(f"tell {TELL} to empty trash")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            gone = (
                self.db.todo(id, include_trashed=True) is None
                and self.db.project(id, include_trashed=True) is None
                and self.db.heading(id, include_trashed=True) is None
                and self.db.area(id) is None
                and self.db.tag_by_id(id) is None
            )
            if gone:
                return
            time.sleep(0.1)

    # -------------------------------------------------------------------- UI

    def show(self, id_or_list: str) -> None:
        body = f"""
        tell {TELL}
            try
                show to do id {as_str(id_or_list)}
            on error
                try
                    show project id {as_str(id_or_list)}
                on error
                    try
                        show area id {as_str(id_or_list)}
                    on error
                        show list {as_str(id_or_list)}
                    end try
                end try
            end try
        end tell
        """
        osa.run(script(body))

    def edit(self, id: str) -> None:
        body = f"tell {TELL} to edit (to do id {as_str(id)})"
        osa.run(body)

    def show_quick_entry(
        self,
        *,
        name: str | None = None,
        notes: str | None = None,
        due_date: date | datetime | str | None = None,
        tags: Iterable[str] | None = None,
        autofill: bool = False,
    ) -> None:
        props = []
        if name is not None:
            props.append(f"name:{as_str(name)}")
        if notes is not None:
            props.append(f"notes:{as_str(notes)}")
        if due_date is not None:
            props.append(f"due date:{as_date(due_date)}")
        if tags is not None:
            props.append(f"tag names:{as_str(_csv_tags(tags))}")
        clauses = []
        if autofill:
            clauses.append("with autofill true")
        if props:
            clauses.append("with properties {" + ", ".join(props) + "}")
        body = f"tell {TELL} to show quick entry panel " + " ".join(clauses)
        osa.run(script(body))

    # -------------------------------------------------------------- maintenance

    def log_completed_now(self) -> None:
        osa.run(f"tell {TELL} to log completed now")

    # ---------------------------------------------------------------- helpers

    def _effective_todo(
        self,
        id: str,
        *,
        name: str | None = None,
        notes: str | None = None,
        due_date=UNSET,
        tags: Iterable[str] | None = None,
        status: Status | None = None,
        project=UNSET,
        area=UNSET,
        _completed_now: bool = False,
        _canceled_now: bool = False,
        _reopen: bool = False,
    ) -> Todo:
        """Build the post-write Todo: read SQLite, apply the patch we just wrote."""
        base = self.db.todo(id, include_trashed=True) or Todo(id=id, name=name or "")
        patch: dict = {}
        if name is not None:
            patch["name"] = name
        if notes is not None:
            patch["notes"] = notes
        if due_date is not UNSET:
            patch["due_date"] = _to_dt(due_date)
        if tags is not None:
            patch["tag_names"] = tuple(tags)
        if status is not None:
            patch["status"] = status
        if project is not UNSET:
            patch["project_id"] = project
        if area is not UNSET:
            patch["area_id"] = area
        if _completed_now:
            patch["completion_date"] = datetime.now()
        if _canceled_now:
            patch["cancellation_date"] = datetime.now()
        if _reopen:
            patch["completion_date"] = None
            patch["cancellation_date"] = None
        return replace(base, **patch)


# ---------------------------------------------------------------------- helpers
# Parsers for AS-driven serialized output.


def _parse_todo(r: list[str]) -> Todo:
    return Todo(
        id=r[0],
        name=r[1],
        notes=r[2],
        status=Status(r[3]) if r[3] in Status._value2member_map_ else Status.OPEN,
        due_date=parse_iso(r[4]),
        activation_date=parse_iso(r[5]),
        completion_date=parse_iso(r[6]),
        cancellation_date=parse_iso(r[7]),
        creation_date=parse_iso(r[8]),
        modification_date=parse_iso(r[9]),
        tag_names=_split_tags(r[10]),
        project_id=r[11] or None,
        area_id=r[12] or None,
        contact_id=r[13] or None,
    )


def _parse_project(r: list[str]) -> Project:
    return Project(
        id=r[0],
        name=r[1],
        notes=r[2],
        status=Status(r[3]) if r[3] in Status._value2member_map_ else Status.OPEN,
        due_date=parse_iso(r[4]),
        activation_date=parse_iso(r[5]),
        completion_date=parse_iso(r[6]),
        cancellation_date=parse_iso(r[7]),
        creation_date=parse_iso(r[8]),
        modification_date=parse_iso(r[9]),
        tag_names=_split_tags(r[10]),
        area_id=r[11] or None,
    )


def _parse_area(r: list[str]) -> Area:
    return Area(
        id=r[0],
        name=r[1],
        tag_names=_split_tags(r[2]),
        collapsed=r[3] == "true",
    )


def _parse_tag(r: list[str]) -> Tag:
    return Tag(
        id=r[0],
        name=r[1],
        parent_id=r[2] or None,
        keyboard_shortcut=r[3] if len(r) > 3 else "",
    )


def _parse_contact(r: list[str]) -> Contact:
    return Contact(id=r[0], name=r[1])
