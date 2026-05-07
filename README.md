# things-sync

Python wrapper for Things 3. Two layers, by what each is best at:

- **`Things`** — façade. Writes go through AppleScript (`osascript`),
  reads through the SQLite layer below. Synchronous against Things'
  local store: a write returns once Things has committed it, so a
  follow-up read sees it immediately.
- **`ThingsDB`** — read-only SQLite at disk speed, reading Things' own
  database. Same dataclasses as the façade returns from writes.

Things 3 must be installed and **running** on the local Mac. There is
no networked / cloud-direct write path — every write is an AS call
against the local app. Reads work whether the app is foreground or
background, but the app process must be alive for writes.

## Install

```bash
uv sync --extra dev
```

(Or, if vendoring into another project, `uv add` from a local path.)

Zero non-stdlib runtime dependencies.

## Quickstart

```python
from things_sync import Things

t = Things()

project = t.create_project("Plan trip", deadline="2026-05-01")
todo = t.create_todo(
    "Book flights",
    notes="aisle seat please",
    deadline="2026-04-29",
    tags=["travel"],
    project=project.id,
)

t.update_todo(todo.id, notes="window seat actually")
t.complete(todo.id)

t.delete(todo.id)        # → Trash (recoverable)
t.empty_trash()          # purge
```

## Entity & operation matrix

| Entity   | Create | Read | Edit fields | Status | Trash / purge |
|----------|--------|------|-------------|--------|---------------|
| Todo     | AS     | DB   | name, notes, due_date, when, tags, project, area, contact | open / completed / canceled | `delete` + `empty_trash` |
| Project  | AS     | DB   | name, notes, due_date, tags, area | open / completed / canceled | `delete` + `empty_trash` |
| Area     | AS     | DB   | name, tags, collapsed | n/a | `delete` |
| Tag      | AS     | DB   | name, shortcut, parent | n/a | `delete` |
| Contact  | AS     | DB   | not exposed | n/a | not exposed |
| Heading  | Shortcut | DB | not exposed | n/a | Shortcut |

### Editing semantics

`update_todo` / `update_project` / `update_area` / `update_tag` accept
keyword arguments only. Pass a value to set it; **omit** the keyword to
leave the field alone.

```python
t.update_todo(todo.id, name="Book trains instead", notes="rebook",
              due_date="2026-05-15", tags=["travel", "urgent"])
t.update_project(project.id, name="Plan adventure",
                 due_date="2026-06-01", area=area.id)
t.update_area(area.id, name="Personal", collapsed=True)
t.update_tag(tag.id, name="errands", shortcut="e")
```

For nullable parents on todos (`project=None`, `area=None`,
`contact=None`) — passing `None` clears the parent. Clearing
`project` or `area` routes the todo back to Inbox (Things' AS
move-to-list, which clears project + area + heading at once).

### Status moves

```python
t.complete(todo.id)
t.cancel(todo.id)
t.reopen(todo.id)
```

For projects, status goes through `update_project(id, status=…)` with
`Status.OPEN` / `Status.COMPLETED` / `Status.CANCELED`.

### Schedule / move

`when` is the start/scheduled date that decides which built-in list
(Today / Upcoming / Anytime / Someday) the todo shows up in.

```python
from datetime import date

t.schedule(todo.id, date.today())            # → Today
t.schedule(todo.id, "2026-05-10")            # → Upcoming, then Today on the day
t.move_to_list(todo.id, "Anytime")           # clear schedule
t.move_to_list(todo.id, "Someday")
t.move_to_list(todo.id, "Inbox")             # also clears project/area
t.move_to_list(todo.id, "Trash")             # → soft-trash
t.move_to_list(todo.id, "Logbook")           # → complete

t.move_to_project(todo.id, project.id)
t.move_to_area(todo.id, area.id)
```

`Upcoming` is derived from a future schedule date — use `schedule`,
not `move_to_list("Upcoming")` (which raises).

### Deletion

```python
t.delete(todo.id)            # soft, → Trash, recoverable
t.empty_trash()              # purges all currently-trashed items
t.delete_immediately(todo.id)  # convenience: trash + empty
```

`delete` works on todos, projects, areas, and tags. (Tags and areas
are removed outright; todos and projects move to Trash.)

## API surface

All methods live on `Things`. Identifiers are Things' own UUIDs
(returned on every create / read).

**Reads.** `version`, `count_todos`, `count_projects`, `count_areas`,
`count_tags`, `lists`, `todos`, `todo`, `todos_in_list`,
`todos_in_project`, `todos_in_area`, `todos_with_tag`, `selected_todos`,
`projects`, `project`, `headings`, `areas`, `area`, `tags`, `tag`,
`contacts`, `exists`.

**Create.** `create_todo`, `create_project`, `create_area`,
`create_tag`, `create_contact`, `parse_quicksilver`.

**Update.** `update_todo`, `update_project`, `update_area`, `update_tag`.

**Status.** `complete`, `cancel`, `reopen`.

**Move / schedule.** `move_to_list`, `move_to_area`, `move_to_project`,
`schedule`.

**Delete.** `delete` (→ Trash for todos/projects, outright for areas/
tags), `empty_trash`, `delete_immediately`.

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
headings   = db.headings()
areas      = db.areas()
tags       = db.tags()
```

The DB is opened in `mode=ro` and is WAL-mode, so reading while the
app is running is safe. Pass `ThingsDB(path=…)` if your install lives
in a non-standard container.

## Models

Frozen dataclasses in `things_sync.models`: `Todo`, `Project`, `Heading`,
`Area`, `Tag`, `Contact`, `ListInfo`, plus the `Status` enum
(`open`/`completed`/`canceled`) and `StartBucket`
(`inbox`/`anytime`/`someday`).

## Known limits

The AS dictionary doesn't cover everything Things' UI exposes; we
keep the wrapper to what AS can actually do reliably:

- **Headings: create + delete via Shortcuts; rename UI-only.**
  AppleScript has no `heading` class at all — neither `make new
  heading` nor `delete (heading id ...)` parse. Both create and
  delete route through Shortcuts.app:
  `Things.create_heading(project_id, name)` shells out to
  `shortcuts run "Things-Sync Add Heading"`, and
  `Things.delete_heading(heading_id)` shells out to
  `shortcuts run "Things-Sync Delete Heading"`. Both Shortcuts
  must be set up once by the user — see the docstrings on the
  two methods for the action recipes. Renaming a heading and
  reassigning todos under one are still UI-only.
- **No programmatic date clearing.** AS rejects
  `set due date of t to missing value` for date-typed properties.
  `update_todo(id, due_date=None)` raises. Clear due dates from
  the Things UI.
- **No `lists` collection enumeration.** The AS `lists` accessor is
  broken on Things 3.22.11; `Things.lists()` returns built-in names
  hardcoded, and `todos_in_list(name)` reads via SQLite.

## Tests

```bash
uv run pytest
```

Live tests run against the Things 3 instance on this machine. Every
created entity is name-prefixed `_ts_test_` and purged in teardown.

**Do not run against a Things account holding real data.** Cleanup
is best-effort and AS bugs in the test suite can leave residue under
the `_ts_test_` prefix.

## How it works

Source layout:

```
src/things_sync/
  __init__.py     — public exports
  models.py       — dataclasses
  things.py       — Things class (façade, AS writers)
  _db.py          — ThingsDB (read-only SQLite)
  _osascript.py   — osascript runner + delimited-output parser
  _scripts.py     — AppleScript prelude + per-class serializers
```

Writes pipe an AppleScript through `osascript -`. The prelude defines
field/record separators (ASCII US `\x1f` and RS `\x1e`) and per-class
serializers; write bodies build a `tell {Things3}` block, mutate the
target entity, and return its serialized record. Reads through
`ThingsDB` open Things' SQLite at
`~/Library/Group Containers/JLMPQHK86H.com.culturedcode.ThingsMac/`
in read-only mode.
