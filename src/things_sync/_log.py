"""Structured operation log for every cloud / mirror touch.

One JSONL line per event at ``~/.cache/things-sync/ops.jsonl``,
append-only. Each line is a single JSON object well under 4 KB so
the POSIX append() write is atomic across concurrent processes —
``atlas watch``, ``atlas hooks session-end``, the crash-test suite,
and any one-off scripts can all log into the same file safely.

Tail it during debugging::

    tail -f ~/.cache/things-sync/ops.jsonl

Set ``THINGS_SYNC_LOG=1`` (or ``stderr``) to also mirror lines to
stderr, useful when you'd otherwise need a second terminal.

Logging never raises — disk-full or permission errors are swallowed
so a misconfigured log doesn't take down a real cloud write.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


LOG_PATH = Path.home() / ".cache/things-sync/ops.jsonl"
_LOG_TO_STDERR = os.environ.get("THINGS_SYNC_LOG", "").lower() in (
    "1", "true", "stderr", "yes",
)

# Truncate so individual notes blobs / huge payloads don't blow up
# log lines. Full payloads can be reconstructed from the cloud
# history if we ever need them.
_MAX_STR = 500
_MAX_LIST = 50


def _truncate(obj: Any) -> Any:
    if isinstance(obj, str):
        if len(obj) > _MAX_STR:
            return obj[:_MAX_STR] + f"…[+{len(obj) - _MAX_STR}]"
        return obj
    if isinstance(obj, dict):
        return {k: _truncate(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        out: list[Any] = [_truncate(v) for v in list(obj)[:_MAX_LIST]]
        if len(obj) > _MAX_LIST:
            out.append(f"…[+{len(obj) - _MAX_LIST}]")
        return out
    return obj


def log_op(op: str, **fields: Any) -> None:
    """Append one structured event to the ops log. Never raises."""
    entry = {
        "ts": datetime.now().isoformat(timespec="milliseconds"),
        "pid": os.getpid(),
        "op": op,
        **{k: _truncate(v) for k, v in fields.items()},
    }
    try:
        line = json.dumps(entry, ensure_ascii=False)
    except (TypeError, ValueError):
        line = json.dumps({"ts": entry["ts"], "op": op, "log_error": "unserializable"})

    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass

    if _LOG_TO_STDERR:
        try:
            sys.stderr.write(line + "\n")
            sys.stderr.flush()
        except OSError:
            pass
