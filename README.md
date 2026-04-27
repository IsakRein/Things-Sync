# things-sync

Python wrapper for Things 3, three layers stacked by what each is best at:

- **`ThingsCloud`** — direct HTTP to `cloud.culturedcode.com`. Every
  write goes through here. Authoritative the moment the POST returns;
  Mac sees the change on its next sync pull. Works without Things
  running. Needs `THINGS_EMAIL` + `THINGS_PASSWORD`.
- **`ThingsDB`** — read-only SQLite at disk speed. Every read goes
  through here.
- **`Things`** — façade that routes calls to the right layer above.
  Falls back to AppleScript only for UI nudges (`show`, `edit`, quick
  entry, launch/quit), `selected_todos`, the few ops cloud doesn't
  cover yet (tag/contact create, area edit), and `empty_trash`.

Mac requires Things 3 installed for SQLite reads + AS UI ops.
`ThingsCloud` works standalone from any machine.

## Install

```bash
uv sync --extra dev
```

(Or, if vendoring into another project, `uv add` from a local path.)

## Quickstart

```python
from things_sync import Things

t = Things()

# Reads
inbox = t.todos_in_list("Inbox")
project = t.create_project("Plan trip", deadline="2026-05-01")
todo = t.create_todo(
    "Book flights",
    notes="aisle seat please",
    deadline="2026-04-29",
    tags=["travel"],
    project=project.id,
)

# Update
t.update_todo(todo.id, notes="window seat actually")

# Status
t.complete(todo.id)

# Soft delete (recoverable in Trash) → hard purge
t.delete(todo.id)
t.empty_trash()
# or convenience:
t.delete_immediately(project.id)
```

## API surface

All methods live on `Things`. Identifiers are Things' own UUIDs (returned
on every create / read).

**Reads.** `version`, `count_todos`, `count_projects`, `count_areas`,
`count_tags`, `lists`, `todos`, `todo`, `todos_in_list`,
`todos_in_project`, `todos_in_area`, `todos_with_tag`, `selected_todos`,
`projects`, `project`, `areas`, `area`, `tags`, `tag`, `contacts`,
`exists`.

**Create.** `create_todo`, `create_project`, `create_area`, `create_tag`,
`create_contact`, `parse_quicksilver`.

**Update.** `update_todo`, `update_project`, `update_area`, `update_tag`.

**Status.** `complete`, `cancel`, `reopen`.

**Move / schedule.** `move_to_list`, `move_to_area`, `move_to_project`,
`schedule`.

**Delete.** `delete` (→ Trash), `empty_trash`, `delete_immediately`.

**UI.** `show`, `edit`, `show_quick_entry`, `launch`, `quit`,
`is_running`.

**Maintenance.** `log_completed_now`.

## Fast reads (`ThingsDB`)

AppleScript enumeration is dominated by per-property IPC roundtrips —
reading every todo with all fields can take seconds per hundred items.
`ThingsDB` skips AppleScript entirely and reads Things' on-disk SQLite
file directly. Same dataclasses, same fields, 100–1000× faster:

```python
from things_sync import ThingsDB, Status

db = ThingsDB()                      # autodetects ~/Library/Group Containers/…
open_todos = [t for t in db.todos() if t.status == Status.OPEN]
projects   = db.projects()
areas      = db.areas()
tags       = db.tags()
```

Reads only. Writes must still go through `Things()` — poking the SQLite
file directly would desynchronise Things' in-memory state and its
CloudKit sync. The DB is WAL-mode so reading while the app is running is
safe. Pass `ThingsDB(path=…)` if your install lives in a non-standard
container.

## Models

Frozen dataclasses in `things_sync.models`: `Todo`, `Project`, `Area`,
`Tag`, `Contact`, `ListInfo`, plus the `Status` enum
(`open`/`completed`/`canceled`).

## Things Cloud HTTP (`ThingsCloud`)

For the operations AppleScript can't do — creating headings, clearing
due dates — and any write you want to make without Things running, use
the cloud client. Works from any machine with `THINGS_EMAIL` and
`THINGS_PASSWORD` set.

```python
from things_sync import Things

t = Things()
t.create_heading("In-Progress", project=project.id)   # AS has no heading API
t.clear_due_date(todo.id)                              # AS refuses missing value
t.trash_heading(heading.id)
```

Or drive the protocol directly:

```python
from things_sync import ThingsCloud

with ThingsCloud.from_env() as cloud:
    todo_uuid = cloud.add_todo("Buy milk", deadline="2026-04-30",
                               project=project_uuid)
    heading_uuid = cloud.add_heading("Done", project=project_uuid)
    cloud.complete(todo_uuid)
    cloud.clear_due_date(todo_uuid)
```

Writes propagate to every device on the next sync pull (typically
seconds to a couple of minutes). The local Mac app sees them via the
same path as any other device — there's no instant read-after-write on
this machine.

A small state file at `~/.cache/things-sync/state.json` caches the
account's history-key, current head index, and our app-instance-id.

## Known AppleScript limits

Things' AppleScript dictionary refuses `missing value` for `date`-typed
properties — `update_todo(id, due_date=...)` can change a deadline but
not clear it. Use `Things.clear_due_date(id)` (HTTP) for that. AS also
has no heading surface at all — use `Things.create_heading(name,
project=...)` and `Things.trash_heading(id)`.

## Tests

```bash
uv run pytest
```

Live tests run against the Things 3 instance on this machine. Every
created entity is name-prefixed `_ts_test_` and purged in teardown.
Tests use `Things(sync_after_write=True)` so creates block until they
land in local SQLite — pacing is bounded by Mac's sync polls. Some
tests are slow (tens of seconds) for that reason.

**Do not run against a Things account holding real data.** Cleanup is
best-effort; the much bigger risk historically was UUID/wire-format
bugs poisoning the Cloud account (history is append-only) and forcing
an account reset to recover. Two safety nets prevent that now —
`_cloud.new_uuid()` only produces ≤16-byte values and
`_cloud._validate_uuids()` rejects non-Base58 strings before they hit
the wire — but the surface area is large; use a sandbox account.

## Read-after-write

Cloud writes are authoritative on the server immediately, but
`ThingsDB` reads reflect Things' local SQLite, which lags by Mac's
sync poll cycle (~5-15s foreground, up to ~3 min idle). For tests or
scripts that care:

```python
t = Things(sync_after_write=True)   # blocks each write until uuid lands locally
```

This calls `Things.launch()` (= `tell ... to activate`) after every
cloud write, which forces an immediate poll and drops the round trip
to ~2.5s. Off by default; in interactive use you usually don't need
to read back what you just wrote.

## How it works

Writes build a JSON commit body and POST to
`cloud.culturedcode.com/version/1/history/<key>/commit`. The protocol
is reverse-engineered (originally captured via mitmproxy); see
`_cloud.py` for the wire-format helpers. UUIDs are 22-char Base58
generated as `secrets.randbits(128)` encoded with leading-`1` pad —
this guarantees ≤16-byte decode (random 22-char Base58 overflows ~37%
of the time and crashes Things).

Reads go through plain SQLite (`sqlite3` stdlib, `mode=ro`) on the
Things database in
`~/Library/Group Containers/JLMPQHK86H.com.culturedcode.ThingsMac/`.

The few methods that still go through AppleScript pipe a script
through `osascript -`; reads serialize records using ASCII unit
(`\x1f`) and record (`\x1e`) separators, parsed back into dataclasses.

Source layout:

```
src/things_sync/
  __init__.py     — public exports
  models.py       — dataclasses
  things.py       — Things class (AppleScript ops + .cloud accessor)
  _db.py          — ThingsDB (read-only SQLite)
  _cloud.py       — ThingsCloud (HTTP write client)
  _osascript.py   — osascript runner + delimited-output parser
  _scripts.py     — AppleScript prelude + per-class serializers
```
