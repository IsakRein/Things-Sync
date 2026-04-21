# things-sync

AppleScript-backed Python wrapper for Things 3. Full CRUD over to-dos,
projects, areas, tags, and contacts via the Things scripting dictionary —
no Cloud HTTP, no reverse-engineered protocols, no Shortcuts wrappers.

Mac only. Things 3 must be installed.

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

## Models

Frozen dataclasses in `things_sync.models`: `Todo`, `Project`, `Area`,
`Tag`, `Contact`, `ListInfo`, plus the `Status` enum
(`open`/`completed`/`canceled`).

## Known AppleScript limits

Things' AppleScript dictionary refuses `missing value` for `date`-typed
properties (`due date`, `activation date`, etc.). You can change a date
to another date via `update_todo(id, due_date=...)`, but you can't clear
it back to "no date" through AppleScript. For that one operation you
need the `things://` URL scheme (`things:///update?id=X&deadline=`)
with Things' auth-token — out of scope here.

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
  things.py       — Things class (all methods)
  _osascript.py   — osascript runner + delimited-output parser
  _scripts.py     — AppleScript prelude + per-class serializers
```
