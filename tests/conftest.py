"""Shared fixtures. Tests run against the live Things 3 sandbox.

Each test gets a fresh `Things()` client. A session-scoped guard records
the IDs of every entity created via the client so we can purge them on
teardown — even if a test fails mid-flight.
"""
from __future__ import annotations

from collections.abc import Iterator

import pytest

from things_sync import Things


PREFIX = "_ts_test_"


@pytest.fixture
def things() -> Iterator[Things]:
    """Fresh client per test."""
    yield Things()


@pytest.fixture(autouse=True)
def _cleanup(things: Things) -> Iterator[None]:
    """After each test: trash anything we created (matched by `_ts_test_` name prefix)."""
    yield
    leftovers: list[str] = []
    for t in things.todos():
        if t.name.startswith(PREFIX):
            leftovers.append(t.id)
    for p in things.projects():
        if p.name.startswith(PREFIX):
            leftovers.append(p.id)
    for a in things.areas():
        if a.name.startswith(PREFIX):
            leftovers.append(a.id)
    for tag in things.tags():
        if tag.name.startswith(PREFIX):
            leftovers.append(tag.id)
    for tid in leftovers:
        try:
            things.delete(tid)
        except Exception:  # noqa: BLE001
            pass
    if leftovers:
        try:
            things.empty_trash()
        except Exception:  # noqa: BLE001
            pass
