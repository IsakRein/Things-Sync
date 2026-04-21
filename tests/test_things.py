"""End-to-end tests against the live Things 3 sandbox.

Tests create entities under the `_ts_test_` name prefix and rely on the
`_cleanup` fixture in conftest to purge them afterwards.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest

from things_sync import Status, Things


PREFIX = "_ts_test_"


def _u(label: str = "") -> str:
    return f"{PREFIX}{label}_{uuid.uuid4().hex[:8]}"


# --------------------------------------------------------------------- meta


def test_version_returns_string(things: Things):
    v = things.version()
    assert v.startswith("3.")


def test_counts_are_ints(things: Things):
    assert things.count_todos() >= 0
    assert things.count_projects() >= 0
    assert things.count_areas() >= 0
    assert things.count_tags() >= 0


def test_lists_includes_builtins(things: Things):
    names = {l.name for l in things.lists()}
    for builtin in ("Inbox", "Today", "Anytime", "Upcoming", "Someday", "Logbook", "Trash"):
        assert builtin in names, f"missing built-in list {builtin!r}"


# ------------------------------------------------------------------- create


def test_create_minimal_todo(things: Things):
    name = _u("minimal")
    t = things.create_todo(name)
    assert t.id
    assert t.name == name
    assert t.status == Status.OPEN
    assert t.notes == ""


def test_create_todo_with_all_fields(things: Things):
    name = _u("full")
    deadline = date.today() + timedelta(days=14)
    t = things.create_todo(
        name,
        notes="multi\nline\nnotes",
        deadline=deadline,
        tags=["test-tag"],
    )
    assert t.name == name
    assert t.notes == "multi\nline\nnotes"
    assert t.due_date is not None
    assert t.due_date.date() == deadline
    assert "test-tag" in t.tag_names


def test_create_project_minimal(things: Things):
    name = _u("proj")
    p = things.create_project(name)
    assert p.id
    assert p.name == name


def test_create_project_with_area(things: Things):
    area = things.create_area(_u("area_for_proj"))
    name = _u("proj_in_area")
    p = things.create_project(name, area=area.id)
    assert p.area_id == area.id


def test_create_area(things: Things):
    name = _u("area")
    a = things.create_area(name)
    assert a.id
    assert a.name == name


def test_create_tag_with_shortcut(things: Things):
    name = _u("tag")
    g = things.create_tag(name, shortcut="t")
    assert g.id
    assert g.name == name
    assert g.keyboard_shortcut == "t"


def test_create_nested_tag(things: Things):
    parent = things.create_tag(_u("parent"))
    child_name = _u("child")
    child = things.create_tag(child_name, parent=parent.name)
    assert child.parent_id == parent.id


def test_parse_quicksilver(things: Things):
    name = _u("qs")
    t = things.parse_quicksilver(name)
    assert t.id
    assert name in t.name


# --------------------------------------------------------------------- read


def test_todo_lookup_by_id(things: Things):
    t = things.create_todo(_u("lookup"))
    fetched = things.todo(t.id)
    assert fetched is not None
    assert fetched.id == t.id
    assert fetched.name == t.name


def test_todo_lookup_missing_returns_none(things: Things):
    assert things.todo("nonexistent-id-xyz") is None


def test_todos_in_inbox_includes_created(things: Things):
    t = things.create_todo(_u("inbox"))
    inbox_ids = {x.id for x in things.todos_in_list("Inbox")}
    assert t.id in inbox_ids


def test_todos_in_project(things: Things):
    p = things.create_project(_u("p"))
    t = things.create_todo(_u("child"), project=p.id)
    children = things.todos_in_project(p.id)
    assert any(x.id == t.id for x in children)


def test_todos_in_area(things: Things):
    a = things.create_area(_u("a"))
    t = things.create_todo(_u("ac"), area=a.id)
    children = things.todos_in_area(a.id)
    assert any(x.id == t.id for x in children)


def test_todos_with_tag(things: Things):
    tag_name = _u("filtertag")
    things.create_tag(tag_name)  # ensure tag exists
    t = things.create_todo(_u("tagged"), tags=[tag_name])
    tagged_ids = {x.id for x in things.todos_with_tag(tag_name)}
    assert t.id in tagged_ids


def test_projects_list_contains_created(things: Things):
    p = things.create_project(_u("listed"))
    assert any(x.id == p.id for x in things.projects())


def test_areas_list_contains_created(things: Things):
    a = things.create_area(_u("listed_area"))
    assert any(x.id == a.id for x in things.areas())


def test_tags_list_contains_created(things: Things):
    g = things.create_tag(_u("listed_tag"))
    assert any(x.id == g.id for x in things.tags())


def test_tag_lookup_by_name(things: Things):
    name = _u("lookup_tag")
    g = things.create_tag(name)
    fetched = things.tag(name)
    assert fetched is not None
    assert fetched.id == g.id


# ------------------------------------------------------------------- update


def test_update_todo_name_and_notes(things: Things):
    t = things.create_todo(_u("upd"), notes="before")
    new_name = _u("upd_after")
    updated = things.update_todo(t.id, name=new_name, notes="after")
    assert updated.name == new_name
    assert updated.notes == "after"


def test_update_todo_change_due_date(things: Things):
    t = things.create_todo(_u("change_due"), deadline=date.today() + timedelta(days=3))
    assert t.due_date is not None
    new_due = date.today() + timedelta(days=10)
    updated = things.update_todo(t.id, due_date=new_due)
    assert updated.due_date is not None
    assert updated.due_date.date() == new_due


def test_update_todo_set_tags_replaces(things: Things):
    t = things.create_todo(_u("retag"), tags=["a", "b"])
    updated = things.update_todo(t.id, tags=["c"])
    assert updated.tag_names == ("c",)


def test_update_project_name(things: Things):
    p = things.create_project(_u("p"))
    new_name = _u("p_renamed")
    updated = things.update_project(p.id, name=new_name)
    assert updated.name == new_name


def test_update_area_collapsed(things: Things):
    a = things.create_area(_u("collapse"))
    updated = things.update_area(a.id, collapsed=True)
    assert updated.collapsed is True


def test_update_tag_rename(things: Things):
    g = things.create_tag(_u("renamable"))
    new_name = _u("renamed")
    updated = things.update_tag(g.id, name=new_name)
    assert updated.name == new_name


# ------------------------------------------------------------- status moves


def test_complete_then_reopen(things: Things):
    t = things.create_todo(_u("complete"))
    completed = things.complete(t.id)
    assert completed.status == Status.COMPLETED
    assert completed.completion_date is not None
    reopened = things.reopen(t.id)
    assert reopened.status == Status.OPEN


def test_cancel(things: Things):
    t = things.create_todo(_u("cancel"))
    canceled = things.cancel(t.id)
    assert canceled.status == Status.CANCELED


def test_move_to_list_today(things: Things):
    t = things.create_todo(_u("move_today"))
    things.move_to_list(t.id, "Today")
    todos_today = {x.id for x in things.todos_in_list("Today")}
    assert t.id in todos_today


def test_move_to_area(things: Things):
    a = things.create_area(_u("move_target"))
    t = things.create_todo(_u("mover"))
    things.move_to_area(t.id, a.id)
    refetched = things.todo(t.id)
    assert refetched is not None
    assert refetched.area_id == a.id


def test_move_to_project(things: Things):
    p = things.create_project(_u("move_target_p"))
    t = things.create_todo(_u("mover_p"))
    things.move_to_project(t.id, p.id)
    refetched = things.todo(t.id)
    assert refetched is not None
    assert refetched.project_id == p.id


def test_schedule_for_today(things: Things):
    t = things.create_todo(_u("scheduled"))
    things.schedule(t.id, date.today())
    refetched = things.todo(t.id)
    assert refetched is not None
    assert refetched.activation_date is not None


# ----------------------------------------------------------------- deletion


def test_soft_delete_moves_to_trash(things: Things):
    t = things.create_todo(_u("trashable"))
    things.delete(t.id)
    trash_ids = {x.id for x in things.todos_in_list("Trash")}
    assert t.id in trash_ids


def test_empty_trash_purges(things: Things):
    t = things.create_todo(_u("purge"))
    things.delete(t.id)
    things.empty_trash()
    trash_ids = {x.id for x in things.todos_in_list("Trash")}
    assert t.id not in trash_ids


def test_delete_immediately(things: Things):
    t = things.create_todo(_u("nuke"))
    things.delete_immediately(t.id)
    assert things.todo(t.id) is None


def test_delete_project(things: Things):
    p = things.create_project(_u("dropproj"))
    things.delete(p.id)
    things.empty_trash()
    assert things.project(p.id) is None


def test_delete_area(things: Things):
    a = things.create_area(_u("droparea"))
    things.delete(a.id)
    assert things.area(a.id) is None


def test_delete_tag(things: Things):
    g = things.create_tag(_u("droptag"))
    things.delete(g.id)
    assert things.tag(g.name) is None


# ---------------------------------------------------------------------- misc


def test_log_completed_now_does_not_raise(things: Things):
    things.log_completed_now()


def test_exists(things: Things):
    t = things.create_todo(_u("exists"))
    assert things.exists(t.id) is True
    assert things.exists("clearly-not-an-id-xyz") is False


def test_selected_todos_returns_list(things: Things):
    # No assertions about content — just shape.
    out = things.selected_todos()
    assert isinstance(out, list)
