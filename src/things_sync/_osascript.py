"""Run AppleScript snippets against Things and parse the unit-separator output."""
from __future__ import annotations

import subprocess
from datetime import datetime

US = "\x1f"  # field separator
RS = "\x1e"  # record separator


class OSAError(RuntimeError):
    """An osascript invocation failed."""


def run(script: str, timeout: float = 30.0) -> str:
    """Run an AppleScript via osascript stdin. Returns stdout (stripped of trailing newline)."""
    try:
        result = subprocess.run(
            ["/usr/bin/osascript", "-"],
            input=script,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise OSAError(f"osascript timed out after {timeout}s") from exc

    if result.returncode != 0:
        raise OSAError(
            f"osascript failed (exit {result.returncode}): {result.stderr.strip()}"
        )
    out = result.stdout
    if out.endswith("\n"):
        out = out[:-1]
    return out


def parse_records(out: str) -> list[list[str]]:
    """Split the wire format into records and fields. Empty input → []."""
    if not out:
        return []
    return [rec.split(US) for rec in out.split(RS) if rec]


def as_str(s: str) -> str:
    """Quote a Python string as an AppleScript string literal."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def as_date(s: str | datetime | None) -> str:
    """Render a Python date/datetime/iso-string as `my parseISO("...")`. None → `missing value`."""
    if s is None:
        return "missing value"
    if isinstance(s, datetime):
        s = s.strftime("%Y-%m-%dT%H:%M:%S")
    elif hasattr(s, "isoformat"):  # date
        s = s.isoformat()
    return f"my parseISO({as_str(s)})"


def parse_iso(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None
