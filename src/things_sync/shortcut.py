"""Back door into Things via the `shortcuts` CLI.

Things exposes 15 App Intents (see `things intents list`). We can't call them
directly from Python — that needs `com.apple.shortcuts.background-running`,
which AMFI blocks for non-Apple binaries. The workaround: author a wrapper
shortcut in Shortcuts.app that chains the Things App Intent we need, then
invoke it via `/usr/bin/shortcuts run`. Shortcuts.app is Apple-signed and
holds the private entitlement; it executes the App Intent on our behalf.

Wrapper shortcuts follow a naming convention so this module can find them.
Default prefix is `ts-` (e.g. `ts-delete`, `ts-edit`). Build them once via
Shortcuts.app — see `things shortcut setup` for the recipe.
"""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

SHORTCUT_PREFIX = "ts-"
SHORTCUTS_DB = Path.home() / "Library/Shortcuts/Shortcuts.sqlite"
_BIN = "/usr/bin/shortcuts"

THINGS_BUNDLE_ID = "com.culturedcode.ThingsMac"


class ShortcutError(RuntimeError):
    pass


class ShortcutNotInstalled(ShortcutError):
    def __init__(self, name: str):
        super().__init__(
            f"Shortcut {name!r} is not installed. Run `things shortcut setup` "
            f"for the wrapper-shortcut recipe."
        )
        self.name = name


@dataclass(frozen=True)
class InstalledShortcut:
    name: str
    associated_bundle_id: str | None
    actions_description: str | None


def _cli_available() -> bool:
    return Path(_BIN).exists() or shutil.which("shortcuts") is not None


def list_installed() -> list[str]:
    """All shortcuts in the user's library (what `shortcuts list` prints)."""
    if not _cli_available():
        raise ShortcutError("/usr/bin/shortcuts not found — needs macOS 12+.")
    out = subprocess.check_output([_BIN, "list"], text=True)
    return [line for line in out.splitlines() if line]


def describe_all() -> list[InstalledShortcut]:
    """Pull name + associated app from the Shortcuts sqlite (more info than `list`)."""
    if not SHORTCUTS_DB.exists():
        return []
    uri = f"file:{SHORTCUTS_DB}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        rows = conn.execute(
            "SELECT ZNAME, ZASSOCIATEDAPPBUNDLEIDENTIFIER, ZACTIONSDESCRIPTION "
            "FROM ZSHORTCUT "
            "WHERE ZTOMBSTONED IS NULL OR ZTOMBSTONED = 0 "
            "ORDER BY ZNAME COLLATE NOCASE"
        ).fetchall()
    finally:
        conn.close()
    return [InstalledShortcut(n, b, d) for (n, b, d) in rows if n]


def is_installed(name: str) -> bool:
    return name in set(list_installed())


def run(
    name: str,
    input_text: str | None = None,
    *,
    input_bytes: bytes | None = None,
    output_type: str | None = "public.plain-text",
    timeout: float = 30.0,
) -> str:
    """Run a wrapper shortcut via `/usr/bin/shortcuts run`.

    Returns the shortcut's output as text (decoded UTF-8, stripped). If
    `output_type` is None, no --output-path is requested and the return
    value is the empty string.
    """
    if not _cli_available():
        raise ShortcutError("/usr/bin/shortcuts not found — needs macOS 12+.")
    if not is_installed(name):
        raise ShortcutNotInstalled(name)

    cmd = [_BIN, "run", name]

    with tempfile.TemporaryDirectory(prefix="things-shortcut-") as td:
        tmp = Path(td)
        if input_bytes is not None:
            ip = tmp / "input.bin"
            ip.write_bytes(input_bytes)
            cmd += ["--input-path", str(ip)]
        elif input_text is not None:
            ip = tmp / "input.txt"
            ip.write_text(input_text, encoding="utf-8")
            cmd += ["--input-path", str(ip)]

        op: Path | None = None
        if output_type is not None:
            op = tmp / "output"
            cmd += ["--output-path", str(op), "--output-type", output_type]

        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise ShortcutError(
                f"shortcut {name!r} failed (exit {proc.returncode}): {detail}"
            )
        if op is None or not op.exists():
            return ""
        return op.read_bytes().decode("utf-8", errors="replace").strip()


# ---------- high-level Things operations ----------


def delete_todo(uuid: str, *, immediate: bool = True) -> None:
    """Delete a to-do via the `{prefix}delete` wrapper shortcut.

    The wrapper must: find the to-do by ID (Shortcut Input) → Delete Items
    (Delete Immediately = on, if `immediate`). See `things shortcut setup`.
    """
    name = f"{SHORTCUT_PREFIX}delete" if immediate else f"{SHORTCUT_PREFIX}trash"
    run(name, input_text=uuid, output_type=None)


SETUP_INSTRUCTIONS = f"""\
One-time setup for the Things back door.

Things ships 15 App Intents (see `things intents list`). macOS only allows
Apple-signed binaries to invoke them directly, so we route through thin
wrapper shortcuts you build once in Shortcuts.app. This module then calls
them headlessly via `/usr/bin/shortcuts run`.

Convention: wrapper shortcuts are named with the prefix `{SHORTCUT_PREFIX}`.

------------------------------------------------------------
Build the delete backdoor: `{SHORTCUT_PREFIX}delete`
------------------------------------------------------------

  1. Open Shortcuts.app → New Shortcut, name it exactly `{SHORTCUT_PREFIX}delete`.
  2. In Shortcut Details (sidebar i icon): set "Accept Text input".
  3. Add action: "Find Things To-Dos".
       - Filter: ID → is → [Shortcut Input]   (drag the magic variable in)
       - Limit: 1 (optional; safer)
  4. Add action: "Delete Items".
       - Items: [Found To-Dos]   (magic variable from step 3)
       - Delete Immediately: On      ← hard delete, bypasses Trash
  5. Save.

Test:
    shortcuts run {SHORTCUT_PREFIX}delete --input-path <(echo <UUID>)
or:
    things trash-hard <UUID>

------------------------------------------------------------
Optional second variant: `{SHORTCUT_PREFIX}trash` (soft delete)
------------------------------------------------------------

Same recipe but with "Delete Immediately: Off". Routes to Trash.

------------------------------------------------------------
Notes
------------------------------------------------------------

 - Running a shortcut does NOT open the Things UI; App Intents advertised
   with `opens app: no` stay headless. `things intents list` shows which.
 - The wrapper shortcut is the *only* privileged hop. If you rename it in
   Shortcuts.app, either rename it back or override SHORTCUT_PREFIX.
 - First invocation may prompt for automation permission (System
   Settings → Privacy & Security → Automation). Accept once; it sticks.
"""
