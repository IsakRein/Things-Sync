"""End-to-end tests for HTTP-only ops: heading create / trash, clear due date.

These wait on Mac's Things app to pull our HTTP commits back from Things
Cloud — typically tens of seconds, sometimes minutes — so they're slow.

Skipped automatically if THINGS_EMAIL / THINGS_PASSWORD are not set.
"""
from __future__ import annotations

import os
import time
import uuid
from datetime import date, timedelta

import pytest

from things_sync import Status, Things, ThingsDB

PREFIX = "_ts_test_"
SYNC_TIMEOUT = 180.0  # seconds


pytestmark = pytest.mark.skipif(
    not (os.environ.get("THINGS_EMAIL") and os.environ.get("THINGS_PASSWORD")),
    reason="THINGS_EMAIL and THINGS_PASSWORD env required for cloud tests",
)


def _u(label: str = "") -> str:
    return f"{PREFIX}{label}_{uuid.uuid4().hex[:8]}"


def _wait(predicate, timeout: float = SYNC_TIMEOUT, *, what: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(2.0)
    raise AssertionError(f"timed out waiting for {what} after {timeout}s")


def test_create_heading_round_trip(things: Things):
    project = things.create_project(_u("heading_parent"))
    name = _u("heading")

    h = things.create_heading(name, project=project.id)
    assert h.id
    assert h.name == name
    assert h.project_id == project.id
    assert h.status == Status.OPEN

    db = ThingsDB()
    _wait(
        lambda: any(x.id == h.id for x in db.headings()),
        what=f"heading {h.id} to land in local DB",
    )
    landed = next(x for x in db.headings() if x.id == h.id)
    assert landed.name == name
    assert landed.project_id == project.id


def test_trash_heading(things: Things):
    project = things.create_project(_u("th_parent"))
    h = things.create_heading(_u("trash_me"), project=project.id)
    db = ThingsDB()
    _wait(
        lambda: any(x.id == h.id for x in db.headings()),
        what="heading to appear before trashing",
    )
    things.trash_heading(h.id)
    _wait(
        lambda: not any(x.id == h.id for x in db.headings()),
        what="heading to disappear after trash",
    )


def test_clear_due_date(things: Things):
    deadline = date.today() + timedelta(days=7)
    t = things.create_todo(_u("deadlined"), deadline=deadline)
    assert t.due_date is not None

    things.clear_due_date(t.id)

    db = ThingsDB()
    _wait(
        lambda: (db_t := db.todos()) and any(
            x.id == t.id and x.due_date is None for x in db_t
        ),
        what="due date to clear in local DB",
    )
