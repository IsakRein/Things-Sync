"""Public Python API: a single ``Things`` class.

Three layers stacked underneath:

- **Writes** → :class:`ThingsCloud` (HTTP commits to ``cloud.culturedcode.com``).
  Authoritative the moment the POST returns. Propagates to all devices
  including this Mac on the next sync pull.
- **Reads** → :class:`ThingsDB` (read-only SQLite at disk speed).
  Reflects whatever has landed in Things' local store; lags writes by
  a few seconds typical, up to ~3 min when Things is idle.
- **AppleScript** — kept for UI nudges and the small surface that
  Cloud doesn't yet cover (creating tags / contacts, parse-quicksilver,
  empty-trash, the virtual built-in lists, currently-selected todos).

After a Cloud write, ``Things.launch()`` (= ``tell ... to activate``)
forces an immediate poll, dropping read-after-write to ~2.5s. Useful
for tests; in interactive use, just rely on Things' normal polling.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
from enum import Enum
from typing import Iterable

from . import _osascript as osa
from ._cloud import (
    DEST_ANYTIME as _DEST_ANYTIME,
    DEST_INBOX as _DEST_INBOX,
    DEST_SOMEDAY as _DEST_SOMEDAY,
    STATUS_CANCELLED as _CLOUD_STATUS_CANCELLED,
    STATUS_COMPLETE as _CLOUD_STATUS_COMPLETE,
    STATUS_OPEN as _CLOUD_STATUS_OPEN,
    ThingsCloud,
)
from ._db import ThingsDB
from ._osascript import US, as_date, as_str, parse_iso, parse_records
from ._scripts import script
from .models import Area, Contact, Heading, ListInfo, Project, Status, Tag, Todo

TELL = 'application id "com.culturedcode.ThingsMac"'
BUILTIN_LISTS = ("Inbox", "Today", "Anytime", "Upcoming", "Someday", "Logbook", "Trash")

_STATUS_TO_CLOUD = {
    Status.OPEN: _CLOUD_STATUS_OPEN,
    Status.CANCELED: _CLOUD_STATUS_CANCELLED,
    Status.COMPLETED: _CLOUD_STATUS_COMPLETE,
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


def _looks_like_uuid(s: str) -> bool:
    """Heuristic: 21-22 chars, all alphanumeric. Used to distinguish a tag
    UUID from a tag name when we accept either at the API surface."""
    return 21 <= len(s) <= 22 and s.isalnum()


class Things:
    """Façade exposing Things' state and ops with sensible per-op routing.

    Writes go through :attr:`cloud` (HTTP). Reads come from :attr:`db`
    (SQLite). UI and a few special-case ops use AppleScript.

    Cloud and DB are lazy: no network or DB I/O until the first call
    that needs them. ``THINGS_EMAIL`` + ``THINGS_PASSWORD`` are required
    in the environment as soon as any write or :attr:`cloud` access
    happens; reads work without them.
    """

    def __init__(self, *, sync_after_write: bool = False, sync_timeout: float = 30.0) -> None:
        """Create a Things facade.

        ``sync_after_write=True`` makes every cloud write call
        :meth:`launch` and poll the local DB until the change lands —
        useful for tests / scripts that want read-after-write
        consistency. Costs ~1-3s per write and may steal focus.
        Off by default; in interactive use you'll just see the change
        after Things' next normal poll.
        """
        self._cloud: ThingsCloud | None = None
        self._db: ThingsDB | None = None
        self._sync_after_write = sync_after_write
        self._sync_timeout = sync_timeout

    @property
    def cloud(self) -> ThingsCloud:
        if self._cloud is None:
            self._cloud = ThingsCloud.from_env()
        return self._cloud

    @property
    def db(self) -> ThingsDB:
        if self._db is None:
            self._db = ThingsDB()
        return self._db

    def _resolve_tags(self, tags: Iterable[str] | None) -> list[str]:
        """Map tag names → tag UUIDs using the local DB. UUIDs pass through.

        Cloud commits expect ``tg: [<uuid>, ...]``; the public API has
        always taken names because that matched the AppleScript surface.
        Anything that doesn't already look like a UUID gets resolved by
        a name lookup against ``ThingsDB.tag(name)``.
        """
        if not tags:
            return []
        out = []
        for t in tags:
            if _looks_like_uuid(t):
                out.append(t)
                continue
            tag = self.db.tag(t)
            if tag is None:
                raise ValueError(
                    f"Tag {t!r} not found in local DB — create it (via "
                    f"create_tag) or pass its UUID directly."
                )
            out.append(tag.id)
        return out

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
        """``tell ... to activate``. Brings Things to foreground and triggers
        an immediate cloud poll — useful right after a cloud write to fast-
        forward the local SQLite copy (~2.5s end-to-end)."""
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
        """Built-in lists. The ids are stable identifiers Things uses
        internally; we return them by name since the AS ``lists``
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
        """Currently-selected todos in Things UI — only available via AS."""
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
        heading: str | None = None,
        contact: str | None = None,
    ) -> Todo:
        """Create a todo via Cloud HTTP. Returns the constructed dataclass."""
        if contact is not None:
            raise NotImplementedError(
                "Cloud wire format for contact assignment isn't captured yet — "
                "create the todo first, then attach via AppleScript if needed."
            )
        tag_uuids = self._resolve_tags(tags)
        uuid = self.cloud.add_todo(
            name, notes=notes or "", when=when, deadline=deadline,
            project=project, area=area, heading=heading,
            tags=tag_uuids,
        )
        self._settle(uuid)
        now = datetime.now()
        return Todo(
            id=uuid, name=name, notes=notes or "",
            due_date=_to_dt(deadline),
            activation_date=_to_dt(when),
            creation_date=now, modification_date=now,
            tag_names=tuple(tags or ()),
            project_id=project, area_id=area, contact_id=None,
            heading_id=heading,
        )

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
        tag_uuids = self._resolve_tags(tags)
        uuid = self.cloud.add_project(
            name, notes=notes or "", deadline=deadline, area=area, tags=tag_uuids,
        )
        # Cloud has no `when` arg on create — schedule afterward if requested.
        if when is not None:
            self.cloud.edit(uuid, when=when)
        self._settle(uuid)
        now = datetime.now()
        return Project(
            id=uuid, name=name, notes=notes or "",
            due_date=_to_dt(deadline),
            activation_date=_to_dt(when),
            creation_date=now, modification_date=now,
            tag_names=tuple(tags or ()),
            area_id=area,
        )

    def create_area(
        self,
        name: str,
        *,
        tags: Iterable[str] | None = None,
    ) -> Area:
        uuid = self.cloud.add_area(name)
        # Cloud area payload doesn't carry tags (we haven't reverse-engineered
        # that yet). Leave tags off for now.
        if tags:
            raise NotImplementedError(
                "Tag assignment on Cloud-created areas isn't wired yet."
            )
        self._settle(uuid)
        return Area(id=uuid, name=name, tag_names=(), collapsed=False)

    def create_heading(self, name: str, *, project: str) -> Heading:
        """Create a heading inside ``project``. Cloud-only — AS has no API."""
        uuid = self.cloud.add_heading(name, project=project)
        self._settle(uuid)
        return Heading(id=uuid, name=name, project_id=project, status=Status.OPEN)

    def create_tag(
        self,
        name: str,
        *,
        parent: str | None = None,
        shortcut: str | None = None,
    ) -> Tag:
        """Create a tag — kept on AppleScript; Cloud wire format not captured."""
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
        """Create a contact — kept on AppleScript; Cloud wire format not captured."""
        body = f"""
        tell {TELL}
            set c to add contact named {as_str(name)}
            return my serializeContact(c)
        end tell
        """
        return _parse_contact(osa.run(script(body)).split(US))

    def parse_quicksilver(self, text: str) -> Todo:
        """Pop the Quick Entry parser — UI-only feature, AS only."""
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
        heading: str | None | _Sentinel = UNSET,  # type: ignore[name-defined]
        contact: str | None | _Sentinel = UNSET,  # type: ignore[name-defined]
    ) -> Todo:
        if contact is not UNSET:
            raise NotImplementedError("Contact assignment via Cloud not wired yet.")
        kwargs: dict = {}
        if name is not None:
            kwargs["title"] = name
        if notes is not None:
            kwargs["notes"] = notes
        if due_date is not UNSET:
            kwargs["deadline"] = due_date
        if tags is not None:
            kwargs["tags"] = self._resolve_tags(tags)
        if status is not None:
            kwargs["status"] = _STATUS_TO_CLOUD[status]
        if project is not UNSET:
            kwargs["project"] = project
        if area is not UNSET:
            kwargs["area"] = area
        if heading is not UNSET:
            kwargs["heading"] = heading
        if kwargs:
            self.cloud.edit(id, **kwargs)
            self._settle(id)
        return self._effective_todo(id, name=name, notes=notes, due_date=due_date,
                                    tags=tags, status=status, project=project,
                                    area=area, heading=heading)

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
        kwargs: dict = {}
        if name is not None:
            kwargs["title"] = name
        if notes is not None:
            kwargs["notes"] = notes
        if due_date is not UNSET:
            kwargs["deadline"] = due_date
        if tags is not None:
            kwargs["tags"] = self._resolve_tags(tags)
        if status is not None:
            kwargs["status"] = _STATUS_TO_CLOUD[status]
        if area is not UNSET:
            kwargs["area"] = area
        if kwargs:
            self.cloud.edit(id, **kwargs)
            self._settle(id)
        base = self.db.project(id, include_trashed=True) or Project(id=id, name=name or "")
        return replace(
            base,
            **({"name": name} if name is not None else {}),
            **({"notes": notes} if notes is not None else {}),
            **({"due_date": _to_dt(due_date)} if due_date is not UNSET else {}),
            **({"tag_names": tuple(tags)} if tags is not None else {}),
            **({"status": status} if status is not None else {}),
            **({"area_id": area} if area is not UNSET else {}),
        )

    def update_area(
        self,
        id: str,
        *,
        name: str | None = None,
        tags: Iterable[str] | None = None,
        collapsed: bool | None = None,
    ) -> Area:
        """Update an area — AS-only (Cloud wire format for Area3 edits not captured)."""
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
        """Rename / re-shortcut / re-parent a tag — AS-only."""
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
        self.cloud.complete(id)
        self._settle(id)
        return self._effective_todo(id, status=Status.COMPLETED, _completed_now=True)

    def cancel(self, id: str) -> Todo:
        self.cloud.cancel(id)
        self._settle(id)
        return self._effective_todo(id, status=Status.CANCELED, _canceled_now=True)

    def reopen(self, id: str) -> Todo:
        self.cloud.reopen(id)
        self._settle(id)
        return self._effective_todo(id, status=Status.OPEN, _reopen=True)

    def move_to_list(self, id: str, list_name: str) -> None:
        """Move a todo to a built-in list via Cloud verbs.

        The lists are virtual — there's no list to "move" to, so we
        translate to the canonical operation:

        - Inbox: clear project/area/heading, set destination=INBOX
        - Today: schedule for today (sr=today; UI shows it under Today)
        - Anytime: clear schedule, destination=ANYTIME
        - Someday: destination=SOMEDAY
        - Logbook: complete (Things archives completed items there)
        - Trash: trash

        ``Upcoming`` isn't a destination — it's auto-derived from a future
        schedule date — so use :meth:`schedule` directly with that date.
        """
        n = list_name.lower()
        if n == "inbox":
            self.cloud.edit(id, project=None, area=None, heading=None,
                            destination=_DEST_INBOX)
        elif n == "today":
            self.cloud.edit(id, when=date.today())
        elif n == "anytime":
            self.cloud.edit(id, when=None, destination=_DEST_ANYTIME)
        elif n == "someday":
            self.cloud.edit(id, destination=_DEST_SOMEDAY)
        elif n == "trash":
            self.cloud.trash(id)
        elif n == "logbook":
            self.cloud.complete(id)
        elif n == "upcoming":
            raise ValueError(
                "Upcoming is derived from a future scheduled date; use "
                "Things.schedule(id, future_date) instead."
            )
        else:
            raise ValueError(
                f"unknown list {list_name!r}; expected Inbox/Today/Anytime/"
                "Someday/Logbook/Trash"
            )
        self._settle(id)

    def move_to_area(self, id: str, area_id: str) -> None:
        self.cloud.edit(id, area=area_id)
        self._settle(id)

    def move_to_project(self, id: str, project_id: str) -> None:
        self.cloud.edit(id, project=project_id)
        self._settle(id)

    def schedule(self, id: str, when: date | datetime | str) -> None:
        self.cloud.edit(id, when=when)
        self._settle(id)

    # -------------------------------------------------------------- deletion

    def delete(self, id: str) -> None:
        """Soft-delete via Cloud (sets ``tr=True``). Recoverable until empty_trash."""
        self.cloud.trash(id)
        self._settle(id, gone=True)

    def trash_heading(self, id: str) -> None:
        self.cloud.trash_heading(id)
        self._settle(id, gone=True)

    def empty_trash(self) -> None:
        """Hard-delete trashed items — AS only (Cloud has no purge verb)."""
        osa.run(f"tell {TELL} to empty trash")

    def delete_immediately(self, id: str) -> None:
        """Soft-trash via Cloud, then empty Trash via AS to purge."""
        self.cloud.trash(id)
        # Empty trash needs Things to have ingested the trash first.
        # The AS empty_trash works on whatever's currently in Trash, so a
        # quick activate-then-empty gets it. Caller should ensure the
        # Cloud trash has propagated (e.g. via launch+wait) for the
        # specific item to be purged.
        self.empty_trash()

    def clear_due_date(self, id: str) -> None:
        """Clear an item's due date — Cloud-only (AS refuses missing-value dates)."""
        self.cloud.clear_due_date(id)

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
        """Sweep completed items to Logbook — AS-only."""
        osa.run(f"tell {TELL} to log completed now")

    # ---------------------------------------------------------------- helpers

    def _settle(self, id: str | None = None, *, gone: bool = False) -> None:
        """If ``sync_after_write`` is on, force a poll and wait for the
        local DB to reflect the change.

        ``gone=True`` waits for the row to become inactive (trashed or
        purged); otherwise waits for it to be active (present, not
        trashed).
        """
        if not self._sync_after_write or id is None:
            return
        import time
        self.launch()  # force foreground poll
        deadline = time.monotonic() + self._sync_timeout
        while time.monotonic() < deadline:
            active = (
                self.db.todo(id, include_trashed=False) is not None
                or self.db.project(id, include_trashed=False) is not None
                or self.db.heading(id, include_trashed=False) is not None
                or self.db.area(id) is not None
                or self.db.tag_by_id(id) is not None
            )
            if gone and not active:
                return
            if not gone and active:
                return
            time.sleep(0.25)

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
        heading=UNSET,
        _completed_now: bool = False,
        _canceled_now: bool = False,
        _reopen: bool = False,
    ) -> Todo:
        """Build the post-write Todo: read SQLite (may be stale), apply our patch.

        Used by ``update_todo`` / ``complete`` / etc. to return a Todo that
        reflects the *intended* state regardless of sync timing.
        """
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
        if heading is not UNSET:
            patch["heading_id"] = heading
        if _completed_now:
            patch["completion_date"] = datetime.now()
        if _canceled_now:
            patch["cancellation_date"] = datetime.now()
        if _reopen:
            patch["completion_date"] = None
            patch["cancellation_date"] = None
        return replace(base, **patch)


# ---------------------------------------------------------------------- helpers
# Parsers retained for AS-driven methods (lists, selected_todos,
# parse_quicksilver, create_tag, create_contact, update_tag, update_area).


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
