"""Shared fixtures. Tests run against the live Things 3 sandbox.

The fixture creates a ``Things(sync_after_write=True)`` so each cloud
write is followed by ``launch()`` + a wait until the row lands in the
local SQLite. That keeps "create then read by id" patterns working
without per-test sleeps, at the cost of a few seconds per write.

Cleanup uses the local DB to find ``_ts_test_*`` leftovers and trashes
them via Cloud. If sync hasn't caught up by the time cleanup runs, a
few items will leak — they get caught by the next test session.
"""
from __future__ import annotations

from collections.abc import Iterator

import pytest

from things_sync import Things


PREFIX = "_ts_test_"


@pytest.fixture
def things() -> Iterator[Things]:
    yield Things(sync_after_write=True, sync_timeout=60.0)


@pytest.fixture(autouse=True)
def _cleanup(things: Things) -> Iterator[None]:
    yield
    leftovers: list[str] = []
    db = things.db
    for t in db.todos():
        if t.name.startswith(PREFIX):
            leftovers.append(t.id)
    for p in db.projects():
        if p.name.startswith(PREFIX):
            leftovers.append(p.id)
    for h in db.headings():
        if h.name.startswith(PREFIX):
            leftovers.append(h.id)
    for a in db.areas():
        if a.name.startswith(PREFIX):
            leftovers.append(a.id)
    for tag in db.tags():
        if tag.name.startswith(PREFIX):
            leftovers.append(tag.id)
    if not leftovers:
        return
    try:
        for uid in leftovers:
            try:
                things.cloud.trash(uid)
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass
