"""Shared fixtures. Tests run against the live Things 3 sandbox.

Each test gets a fresh ``Things`` client. Cleanup uses the Cloud's
authoritative server view to trash any ``_ts_test_*`` items the test
created — this avoids the ``ThingsDB`` sync-lag race (Mac may not have
pulled the just-written items yet).
"""
from __future__ import annotations

from collections.abc import Iterator

import pytest

from things_sync import Things


PREFIX = "_ts_test_"


@pytest.fixture
def things() -> Iterator[Things]:
    """Cloud-write + DB-read with sync_after_write so tests can read what
    they just wrote without explicit waits."""
    yield Things(sync_after_write=True)


@pytest.fixture(autouse=True)
def _cleanup(things: Things) -> Iterator[None]:
    """Trash anything we created via Cloud trash. Items still untrashed and
    matching the ``_ts_test_`` prefix get a ``tr=True`` commit. Server is
    authoritative — no waiting for Mac to sync."""
    yield
    try:
        items = things.cloud.replay()
    except Exception:  # noqa: BLE001 — cloud unavailable, skip cleanup
        return
    for uuid, p in items.items():
        if p.get("tr"):  # already trashed
            continue
        title = p.get("tt") or ""
        if not isinstance(title, str) or not title.startswith(PREFIX):
            continue
        try:
            things.cloud.trash(uuid)
        except Exception:  # noqa: BLE001
            pass
