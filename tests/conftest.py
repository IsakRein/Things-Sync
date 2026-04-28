"""Shared fixtures. Tests run against the live Things 3 sandbox.

AppleScript writes are synchronous against Things' local store, so a
write returns once Things has committed it to TMTask — no settle loop
needed.

Cleanup walks the local DB for ``_ts_test_*`` leftovers and trashes
them.
"""
from __future__ import annotations

from collections.abc import Iterator

import pytest

from things_sync import Things


PREFIX = "_ts_test_"


@pytest.fixture
def things() -> Iterator[Things]:
    yield Things()


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
    for uid in leftovers:
        try:
            things.delete(uid)
        except Exception:  # noqa: BLE001
            pass
