"""Public Python API: a single `Things` class wrapping every Things 3 AppleScript op."""
from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Iterable

from . import _osascript as osa
from ._osascript import US, RS, as_str, as_date, parse_iso, parse_records
from ._scripts import script
from .models import Area, Contact, ListInfo, Project, Status, Tag, Todo

TELL = 'application id "com.culturedcode.ThingsMac"'
BUILTIN_LISTS = ("Inbox", "Today", "Anytime", "Upcoming", "Someday", "Logbook", "Trash")


class _Sentinel(Enum):
    UNSET = object()


UNSET = UNSET


def _to_date_arg(v: date | datetime | str | None) -> str:
    return as_date(v)


def _csv_tags(tags: Iterable[str] | None) -> str:
    if tags is None:
        return ""
    return ",".join(tags)


def _split_tags(s: str) -> tuple[str, ...]:
    if not s:
        return ()
    return tuple(t.strip() for t in s.split(",") if t.strip())


class Things:
    """Synchronous wrapper around Things 3's AppleScript dictionary."""

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
        osa.run(f"tell {TELL} to activate")

    # ----------------------------------------------------------------- reads

    def todos(self) -> list[Todo]:
        body = f"""
        tell {TELL}
            set out to ""
            repeat with t in to dos
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

    def todo(self, id: str) -> Todo | None:
        body = f"""
        tell {TELL}
            try
                set t to to do id {as_str(id)}
                return my serializeTodo(t)
            on error
                return ""
            end try
        end tell
        """
        out = osa.run(script(body))
        if not out:
            return None
        return _parse_todo(out.split(US))

    def todos_in_list(self, name: str) -> list[Todo]:
        body = f"""
        tell {TELL}
            set out to ""
            repeat with t in to dos of list {as_str(name)}
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

    def todos_in_project(self, id: str) -> list[Todo]:
        body = f"""
        tell {TELL}
            set out to ""
            repeat with t in to dos of project id {as_str(id)}
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

    def todos_in_area(self, id: str) -> list[Todo]:
        body = f"""
        tell {TELL}
            set out to ""
            repeat with t in to dos of area id {as_str(id)}
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

    def todos_with_tag(self, name: str) -> list[Todo]:
        body = f"""
        tell {TELL}
            set out to ""
            repeat with t in to dos of tag {as_str(name)}
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

    def projects(self) -> list[Project]:
        body = f"""
        tell {TELL}
            set out to ""
            repeat with p in projects
                if out is "" then
                    set out to my serializeProject(p)
                else
                    set out to out & RS & my serializeProject(p)
                end if
            end repeat
            return out
        end tell
        """
        return [_parse_project(r) for r in parse_records(osa.run(script(body)))]

    def project(self, id: str) -> Project | None:
        body = f"""
        tell {TELL}
            try
                set p to project id {as_str(id)}
                return my serializeProject(p)
            on error
                return ""
            end try
        end tell
        """
        out = osa.run(script(body))
        if not out:
            return None
        return _parse_project(out.split(US))

    def areas(self) -> list[Area]:
        body = f"""
        tell {TELL}
            set out to ""
            repeat with a in areas
                if out is "" then
                    set out to my serializeArea(a)
                else
                    set out to out & RS & my serializeArea(a)
                end if
            end repeat
            return out
        end tell
        """
        return [_parse_area(r) for r in parse_records(osa.run(script(body)))]

    def area(self, id: str) -> Area | None:
        body = f"""
        tell {TELL}
            try
                set a to area id {as_str(id)}
                return my serializeArea(a)
            on error
                return ""
            end try
        end tell
        """
        out = osa.run(script(body))
        if not out:
            return None
        return _parse_area(out.split(US))

    def tags(self) -> list[Tag]:
        body = f"""
        tell {TELL}
            set out to ""
            repeat with g in tags
                if out is "" then
                    set out to my serializeTag(g)
                else
                    set out to out & RS & my serializeTag(g)
                end if
            end repeat
            return out
        end tell
        """
        return [_parse_tag(r) for r in parse_records(osa.run(script(body)))]

    def tag(self, name: str) -> Tag | None:
        body = f"""
        tell {TELL}
            try
                set g to tag {as_str(name)}
                return my serializeTag(g)
            on error
                return ""
            end try
        end tell
        """
        out = osa.run(script(body))
        if not out:
            return None
        return _parse_tag(out.split(US))

    def contacts(self) -> list[Contact]:
        body = f"""
        tell {TELL}
            set out to ""
            repeat with c in contacts
                if out is "" then
                    set out to my serializeContact(c)
                else
                    set out to out & RS & my serializeContact(c)
                end if
            end repeat
            return out
        end tell
        """
        return [_parse_contact(r) for r in parse_records(osa.run(script(body)))]

    def lists(self) -> list[ListInfo]:
        body = f"""
        tell {TELL}
            set out to ""
            repeat with l in lists
                if out is "" then
                    set out to my serializeList(l)
                else
                    set out to out & RS & my serializeList(l)
                end if
            end repeat
            return out
        end tell
        """
        return [
            ListInfo(id=r[0], name=r[1])
            for r in parse_records(osa.run(script(body)))
        ]

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
            props.append(f"due date:{_to_date_arg(deadline)}")
        if tags is not None:
            props.append(f"tag names:{as_str(_csv_tags(tags))}")
        record = "{" + ", ".join(props) + "}"

        location = ""
        if project is not None:
            location = f" at end of to dos of project id {as_str(project)}"
        elif area is not None:
            location = f" at end of to dos of area id {as_str(area)}"

        post = []
        if when is not None:
            post.append(f"schedule t for {_to_date_arg(when)}")
        if contact is not None:
            post.append(f"set contact of t to contact id {as_str(contact)}")

        post_block = "\n            ".join(post)
        body = f"""
        tell {TELL}
            set t to make new to do with properties {record}{location}
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
            props.append(f"due date:{_to_date_arg(deadline)}")
        if tags is not None:
            props.append(f"tag names:{as_str(_csv_tags(tags))}")
        record = "{" + ", ".join(props) + "}"

        location = ""
        if area is not None:
            location = f" at end of projects of area id {as_str(area)}"

        post = []
        if when is not None:
            post.append(f"schedule p for {_to_date_arg(when)}")

        post_block = "\n            ".join(post)
        body = f"""
        tell {TELL}
            set p to make new project with properties {record}{location}
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
        if tags is not None:
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

    def parse_quicksilver(self, text: str) -> Todo:
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
        sets = _todo_setters(name=name, notes=notes, due_date=due_date, tags=tags, status=status)
        sets += _ref_setters(project=project, area=area, contact=contact)
        if not sets:
            t = self.todo(id)
            assert t, f"todo {id!r} not found"
            return t
        sets_block = "\n            ".join(sets)
        body = f"""
        tell {TELL}
            set t to to do id {as_str(id)}
            {sets_block}
            return my serializeTodo(t)
        end tell
        """
        return _parse_todo(osa.run(script(body)).split(US))

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
        sets = _todo_setters(name=name, notes=notes, due_date=due_date, tags=tags, status=status)
        sets += _ref_setters(area=area)
        if not sets:
            p = self.project(id)
            assert p, f"project {id!r} not found"
            return p
        sets_block = "\n            ".join(sets)
        body = f"""
        tell {TELL}
            set p to project id {as_str(id)}
            set t to p
            {sets_block}
            return my serializeProject(p)
        end tell
        """
        return _parse_project(osa.run(script(body)).split(US))

    def update_area(
        self,
        id: str,
        *,
        name: str | None = None,
        tags: Iterable[str] | None = None,
        collapsed: bool | None = None,
    ) -> Area:
        sets = []
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
        sets = []
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
        return self.update_todo(id, status=Status.COMPLETED)

    def cancel(self, id: str) -> Todo:
        return self.update_todo(id, status=Status.CANCELED)

    def reopen(self, id: str) -> Todo:
        return self.update_todo(id, status=Status.OPEN)

    def move_to_list(self, id: str, list_name: str) -> None:
        body = f"""
        tell {TELL}
            move (to do id {as_str(id)}) to list {as_str(list_name)}
        end tell
        """
        osa.run(script(body))

    def move_to_area(self, id: str, area_id: str) -> None:
        body = f"""
        tell {TELL}
            set area of (to do id {as_str(id)}) to area id {as_str(area_id)}
        end tell
        """
        osa.run(script(body))

    def move_to_project(self, id: str, project_id: str) -> None:
        body = f"""
        tell {TELL}
            set project of (to do id {as_str(id)}) to project id {as_str(project_id)}
        end tell
        """
        osa.run(script(body))

    def schedule(self, id: str, when: date | datetime | str) -> None:
        body = f"""
        tell {TELL}
            schedule (to do id {as_str(id)}) for {_to_date_arg(when)}
        end tell
        """
        osa.run(script(body))

    # -------------------------------------------------------------- deletion

    def delete(self, id: str) -> None:
        """Move to trash (NSDeleteCommand). Recoverable until empty_trash()."""
        body = f"""
        tell {TELL}
            try
                delete (to do id {as_str(id)})
            on error
                try
                    delete (project id {as_str(id)})
                on error
                    try
                        delete (area id {as_str(id)})
                    on error
                        delete (tag id {as_str(id)})
                    end try
                end try
            end try
        end tell
        """
        osa.run(script(body))

    def empty_trash(self) -> None:
        osa.run(f"tell {TELL} to empty trash")

    def delete_immediately(self, id: str) -> None:
        self.delete(id)
        self.empty_trash()

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
            props.append(f"due date:{_to_date_arg(due_date)}")
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

    def count_todos(self) -> int:
        return int(osa.run(f"tell {TELL} to return (count of to dos) as text"))

    def count_projects(self) -> int:
        return int(osa.run(f"tell {TELL} to return (count of projects) as text"))

    def count_areas(self) -> int:
        return int(osa.run(f"tell {TELL} to return (count of areas) as text"))

    def count_tags(self) -> int:
        return int(osa.run(f"tell {TELL} to return (count of tags) as text"))

    def exists(self, id: str) -> bool:
        body = f"""
        tell {TELL}
            try
                set _ to id of (to do id {as_str(id)})
                return "true"
            on error
                try
                    set _ to id of (project id {as_str(id)})
                    return "true"
                on error
                    try
                        set _ to id of (area id {as_str(id)})
                        return "true"
                    on error
                        return "false"
                    end try
                end try
            end try
        end tell
        """
        return osa.run(body) == "true"


# ---------------------------------------------------------------------- helpers


def _todo_setters(*, name, notes, due_date, tags, status):
    out = []
    if name is not None:
        out.append(f"set name of t to {as_str(name)}")
    if notes is not None:
        out.append(f"set notes of t to {as_str(notes)}")
    if due_date is not UNSET:
        out.append(f"set due date of t to {_to_date_arg(due_date)}")
    if tags is not None:
        out.append(f"set tag names of t to {as_str(_csv_tags(tags))}")
    if status is not None:
        out.append(f"set status of t to {status.value}")
    return out


def _ref_setters(*, project=UNSET, area=UNSET, contact=UNSET):
    out = []
    if project is not UNSET:
        if project is None:
            out.append("set project of t to missing value")
        else:
            out.append(f"set project of t to project id {as_str(project)}")
    if area is not UNSET:
        if area is None:
            out.append("set area of t to missing value")
        else:
            out.append(f"set area of t to area id {as_str(area)}")
    if contact is not UNSET:
        if contact is None:
            out.append("set contact of t to missing value")
        else:
            out.append(f"set contact of t to contact id {as_str(contact)}")
    return out


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
