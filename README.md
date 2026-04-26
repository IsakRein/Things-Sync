# things-sync

Python wrapper for Things 3, three layers stacked by what each is best at:

- **`Things`** — AppleScript dictionary, for everything in Things' scripting
  surface (todos, projects, areas, tags, contacts) and the UI ops only a
  running app can do (`show`, `edit`, `show_quick_entry`).
- **`ThingsDB`** — read-only SQLite, for bulk enumeration at disk speed.
- **`ThingsCloud`** — direct HTTP to `cloud.culturedcode.com`, for the few
  ops AppleScript can't: creating headings, clearing due dates, and any
  write you want to do without Things running.

Mac requires Things 3 installed for AppleScript / SQLite. `ThingsCloud`
needs `THINGS_EMAIL` + `THINGS_PASSWORD` and works from anywhere.

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

42 live tests run against the Things 3 instance on this machine. Every
created entity is name-prefixed `_ts_test_` and purged in teardown, so
re-running the suite is idempotent. **Do not run against a Things
account holding real data** — even though cleanup is best-effort, a
crashed test could leave stray items in your Trash.

## How it works

Each method assembles an AppleScript snippet (prelude with helpers +
operation body) and pipes it to `osascript -`. Reads serialize records
using ASCII unit (`\x1f`) and record (`\x1e`) separators, parsed back
into dataclasses on the Python side.

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
