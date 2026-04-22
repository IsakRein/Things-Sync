"""Smoke tests for the SQLite reader.

Runs against the live Things 3 database. Unlike `test_things.py` these
tests do not create entities — the reader is read-only — so they're safe
to run without the cleanup harness.
"""
from __future__ import annotations

from datetime import datetime

from things_sync import Status, Things, ThingsDB
from things_sync._db import _decode_packed_date


def test_packed_date_decodes_sample_values():
    # Values captured from real Things deadlines.
    assert _decode_packed_date(132797568) == datetime(2026, 5, 9)
    assert _decode_packed_date(132799616) == datetime(2026, 5, 25)
    assert _decode_packed_date(132808576) == datetime(2026, 7, 31)
    assert _decode_packed_date(None) is None
    assert _decode_packed_date(0) is None


def test_db_path_autodetects():
    db = ThingsDB()
    assert db.path.exists(), f"autodetected path does not exist: {db.path}"
    assert db.path.name == "main.sqlite"


def test_reads_return_expected_types():
    db = ThingsDB()
    for td in db.todos():
        assert td.id and isinstance(td.id, str)
        assert isinstance(td.name, str)
        assert td.status in (Status.OPEN, Status.COMPLETED, Status.CANCELED)
        assert td.due_date is None or isinstance(td.due_date, datetime)
    for p in db.projects():
        assert p.id and isinstance(p.id, str)
        assert p.status in (Status.OPEN, Status.COMPLETED, Status.CANCELED)
    for a in db.areas():
        assert a.id and isinstance(a.id, str)
        assert isinstance(a.name, str)


def test_area_ids_match_applescript(things: Things):
    """DB and AppleScript should see the same areas."""
    db_ids = {a.id for a in ThingsDB().areas()}
    as_ids = {a.id for a in things.areas()}
    assert db_ids == as_ids


def test_project_ids_match_applescript(things: Things):
    """DB and AppleScript should see the same non-trashed projects."""
    db_ids = {p.id for p in ThingsDB().projects()}
    as_ids = {p.id for p in things.projects()}
    # AppleScript's `projects` enumerates non-trashed projects regardless
    # of status; DB reader does the same by default. Exact set match.
    assert db_ids == as_ids
