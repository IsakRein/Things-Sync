# things-sync

Python wrapper for Things 3. Four layers stacked by what each is best at:

- **`ThingsCloud`** — direct HTTP to `cloud.culturedcode.com`. Every
  write goes through here. Authoritative the moment the POST returns;
  Mac sees the change on its next sync pull. Works without Things
  running. Needs `THINGS_EMAIL` + `THINGS_PASSWORD`.
- **`ThingsDB`** — read-only SQLite at disk speed, reading Things'
  own database. Fast, but only sees what Things.app has synced.
- **`ThingsMirror`** — local SQLite cache of the cloud commit stream.
  Same shape as `ThingsDB`, but reads come from cloud history we
  pull and replay ourselves — no dependency on Things being open or
  caught up. Writes feed it through a hook so reads see them
  immediately.
- **`Things`** — façade that routes calls to the right layer above.
  Falls back to AppleScript only for UI nudges (`show`, `edit`, quick
  entry, launch/quit), `selected_todos`, the few ops cloud doesn't
  cover yet (tag/contact create, area edit, tag edit), and
  `empty_trash`.

`ThingsCloud` and `ThingsMirror` work standalone from any machine.
`ThingsDB` and the AS-only ops in `Things` need Things 3 installed.

## Install

```bash
uv sync --extra dev
```

(Or, if vendoring into another project, `uv add` from a local path.)

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

What's supported on each entity type today, and which layer carries it.
Cloud == HTTP commit (works without Things running). AS == AppleScript.
DB == read-only SQLite.

| Entity   | Create                | Read              | Edit fields                                              | Status (open/complete/cancel/reopen) | Trash / purge          |
|----------|-----------------------|-------------------|----------------------------------------------------------|--------------------------------------|------------------------|
| Todo     | Cloud                 | DB                | name, notes, due_date, when (schedule), tags, project, area, heading | Cloud (`complete`/`cancel`/`reopen`) | Cloud (`delete`) + AS (`empty_trash`) |
| Project  | Cloud                 | DB                | name, notes, due_date, tags, area                        | Cloud (`complete`/`cancel`/`reopen` via `update_project(status=…)`) | Cloud (`delete`) + AS (`empty_trash`) |
| Heading  | Cloud (`create_heading`) | DB             | name (via `t.cloud.edit(id, title=…)`)†                  | n/a                                  | Cloud (`trash_heading`) |
| Area     | Cloud (`create_area`) | DB                | name, tags, collapsed (AS only)                          | n/a                                  | not exposed            |
| Tag      | AS (`create_tag`)     | DB                | name, shortcut, parent (AS only)                         | n/a                                  | not exposed            |
| Contact  | AS (`create_contact`) | DB                | not exposed                                              | n/a                                  | not exposed            |

† Headings rename through the raw cloud client because Things treats
them as `Task6` entities (same wire format as todos/projects). The
façade exposes `create_heading` and `trash_heading`; for renames use
`t.cloud.edit(heading_id, title="New name")`.

### Editing semantics

`update_todo` / `update_project` / `update_area` / `update_tag` accept
keyword arguments only. Pass a value to set it; **omit** the keyword to
leave the field alone.

For nullable fields on todos and projects (`due_date`, `project`,
`area`, `heading`, `contact`), pass `None` to clear:

```python
t.update_todo(todo.id, due_date=None, project=None, heading=None)
```

Cleared dates work on todos (`update_todo(id, due_date=None)` clears
the deadline via Cloud). Note that AppleScript can't clear dates at
all; the wrapper avoids that path. There's also a one-off
`Things.clear_due_date(id)` if you only need that operation.

Renaming, re-dating, re-noting are all the same call:

```python
t.update_todo(todo.id, name="Book trains instead", notes="rebook",
              due_date="2026-05-15", tags=["travel", "urgent"])
t.update_project(project.id, name="Plan adventure",
                 due_date="2026-06-01", area=area.id)
t.update_area(area.id, name="Personal", collapsed=True)
t.update_tag(tag.id, name="errands", shortcut="e")
```

### Status moves

```python
t.complete(todo.id)
t.cancel(todo.id)
t.reopen(todo.id)
```

For projects, status goes through `update_project(id, status=…)` with
`Status.OPEN` / `Status.COMPLETED` / `Status.CANCELED`.

### Schedule / move

Not the same as setting a due date — `when` is the start/scheduled
date that decides which built-in list (Today / Upcoming / Anytime /
Someday) the todo shows up in.

```python
from datetime import date

t.schedule(todo.id, date.today())            # → Today
t.schedule(todo.id, "2026-05-10")            # → Upcoming, then Today on the day
t.move_to_list(todo.id, "Anytime")           # clear schedule
t.move_to_list(todo.id, "Someday")
t.move_to_list(todo.id, "Inbox")             # also clears project/area/heading
t.move_to_list(todo.id, "Trash")             # → soft-trash via Cloud
t.move_to_list(todo.id, "Logbook")           # → complete

t.move_to_project(todo.id, project.id)
t.move_to_area(todo.id, area.id)
```

`Upcoming` is derived from a future schedule date, not a destination —
use `schedule(id, future_date)` rather than `move_to_list(id,
"Upcoming")` (which raises).

### Deletion

```python
t.delete(todo.id)            # soft, → Trash, recoverable
t.empty_trash()              # AS-only: purges all currently-trashed items
t.delete_immediately(todo.id)  # convenience: trash + empty
```

Soft-trash works on todos, projects, and headings. Heading purge has a
dedicated `trash_heading(id)`. Areas, tags, and contacts have no
exposed delete path today.

## API surface

All methods live on `Things`. Identifiers are Things' own UUIDs (returned
on every create / read).

**Reads.** `version`, `count_todos`, `count_projects`, `count_areas`,
`count_tags`, `lists`, `todos`, `todo`, `todos_in_list`,
`todos_in_project`, `todos_in_area`, `todos_with_tag`, `selected_todos`,
`projects`, `project`, `headings`, `areas`, `area`, `tags`, `tag`,
`contacts`, `exists`.

**Create.** `create_todo`, `create_project`, `create_heading`,
`create_area`, `create_tag`, `create_contact`, `parse_quicksilver`.

**Update.** `update_todo`, `update_project`, `update_area`, `update_tag`.
(Heading rename → `t.cloud.edit(id, title=…)`.)

**Status.** `complete`, `cancel`, `reopen`.

**Move / schedule.** `move_to_list`, `move_to_area`, `move_to_project`,
`schedule`.

**Delete.** `delete` (→ Trash), `trash_heading`, `empty_trash`,
`delete_immediately`, `clear_due_date`.

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

Reads only. Writes must still go through `Things()` — poking the SQLite
file directly would desynchronise Things' in-memory state and its
CloudKit sync. The DB is WAL-mode so reading while the app is running is
safe. Pass `ThingsDB(path=…)` if your install lives in a non-standard
container.

## Cloud-direct reads (`ThingsMirror`)

`ThingsDB` reads from Things' SQLite, so it only sees what Things.app
has synced — and only on a Mac with Things installed. `ThingsMirror`
skips Things entirely: it pulls the same commit stream cloud pushes to
every device, replays it into a local SQLite we own
(`~/.cache/things-sync/mirror.sqlite`), and serves the same
dataclasses `ThingsDB` does.

```python
from things_sync import ThingsCloud, ThingsMirror

with ThingsCloud.from_env() as cloud:
    m = ThingsMirror(cloud)          # auto-wires the write hook
    m.pull()                         # catch up from cloud history
    m.todos()                        # disk-speed reads, no Things.app

    uuid = cloud.add_todo("Buy milk")
    m.todo(uuid)                     # visible immediately
```

Same surface as `ThingsDB` — `todos / projects / headings / areas /
tags`, by-id lookups, `todos_in_project / todos_in_area /
todos_under_heading / todos_with_tag`, `count_*`, `exists`. Call sites
that already use `ThingsDB` can swap to `ThingsMirror` with no other
changes.

`pull()` is incremental and idempotent. Cloud-side history is
server-compacted, so even on accounts with thousands of historical
commits a fresh `reset()` replays in a handful of items.

The mirror's cursor (`applied_index`) lives in the mirror DB,
separate from `CloudState.head_index` (the write-side ancestor
cursor). It auto-wipes on cloud-account change, the same protection
`CloudState` has.

Construction registers `cloud._commit_hook = mirror._on_commit`, so
every successful write also lands in the mirror — reads see writes
immediately, no fetch round-trip and no sync wait. Pass
`attach=False` to opt out.

## Models

Frozen dataclasses in `things_sync.models`: `Todo`, `Project`, `Heading`,
`Area`, `Tag`, `Contact`, `ListInfo`, plus the `Status` enum
(`open`/`completed`/`canceled`) and `StartBucket`
(`inbox`/`anytime`/`someday`).

## Things Cloud HTTP (`ThingsCloud`)

For the operations the façade doesn't expose, or any write you want to
make without Things running, drop down to the cloud client. Works from
any machine with `THINGS_EMAIL` and `THINGS_PASSWORD` set.

```python
from things_sync import ThingsCloud

with ThingsCloud.from_env() as cloud:
    todo_uuid    = cloud.add_todo("Buy milk", deadline="2026-04-30",
                                  project=project_uuid)
    project_uuid = cloud.add_project("Plan trip")
    heading_uuid = cloud.add_heading("Done", project=project_uuid)
    area_uuid    = cloud.add_area("Personal")

    cloud.edit(todo_uuid, title="Buy oat milk", notes="…",
               deadline="2026-05-01", tags=[tag_uuid],
               project=project_uuid, heading=heading_uuid)
    cloud.edit(heading_uuid, title="Completed")  # rename a heading

    cloud.complete(todo_uuid)
    cloud.clear_due_date(todo_uuid)
    cloud.trash(todo_uuid)
    cloud.untrash(todo_uuid)
```

`cloud.edit()` is the single-entry edit verb; it patches any `Task6`
entity (todo, project, or heading). For nullable fields (`when`,
`deadline`, `project`, `area`, `heading`) `None` clears, a value sets,
and the default sentinel `False` leaves the field alone.

Writes propagate to every device on the next sync pull (typically
seconds to a couple of minutes). The local Mac app sees them via the
same path as any other device — there's no instant read-after-write on
this machine.

A small state file at `~/.cache/things-sync/state.json` caches the
account's history-key, current head index, and our app-instance-id.

## Read-after-write

Cloud writes are authoritative on the server immediately, but
`ThingsDB` reads reflect Things' local SQLite, which lags by Mac's
sync poll cycle (~5-15s foreground, up to ~3 min idle). Three ways
to bridge the gap, depending on the read path:

- **`ThingsMirror`** (preferred for new code) — the write hook lands
  every commit locally too, so reads from the mirror see the new
  state immediately. No Things.app, no sync wait.
- `Things(sync_after_write=True)` — blocks each write until the uuid
  lands in Things' SQLite. Uses `Things.launch()` (= `tell ... to
  activate`) which forces an immediate poll and drops the round trip
  to ~2.5s; downside is it foregrounds Things on every write.
- `Things().launch()` ad-hoc — same trick, one-shot.

Mirror reads sidestep this entirely; the others are still useful when
the consumer already reads from `ThingsDB` or doesn't run a mirror.

## Operation log

Every cloud touch and every mirror state change is appended as one
JSON line to `~/.cache/things-sync/ops.jsonl`. This is the audit
trail to consult when something crashes Things or a write goes
missing — the file is the source of truth for what we sent, in what
order, and what happened.

Events:

| op | when |
| --- | --- |
| `fetch.start` / `fetch.ok` / `fetch.error` | every cloud GET |
| `commit.start` | every cloud POST (full `body.p`, truncated at 500 chars/field) |
| `commit.retry` | 409/410/412 stale-ancestor; logs sleep ms + new head |
| `commit.ok` / `commit.error` / `commit.exhausted` | terminal states |
| `mirror.pull.start` / `mirror.pull.ok` / `mirror.pull.error` | mirror sync |
| `mirror.on_commit` | write-side hook applies a commit locally; flag `fast_path` distinguishes the in-place apply from a full pull |

Each entry has `ts` (millisecond ISO), `pid`, `op`, plus op-specific
fields. Each `commit.*` shares an `id` so you can join start ↔ retry
↔ ok/error lines.

Properties:

- **Append-only and concurrent-safe**: a single `json.dumps` line
  fits well under `PIPE_BUF`, so multi-process writers (`atlas
  watch`, hooks, ad-hoc scripts, the crash-test suite) can all log
  to the same file without corruption.
- **Never raises**: disk-full or permission errors are swallowed so
  a broken log can't take down a real cloud write.
- **Truncation**: long strings cap at 500 chars, lists at 50 entries
  — log stays scannable; the full payload still exists on the cloud
  history.

Tail it:

```bash
tail -f ~/.cache/things-sync/ops.jsonl
# or filter:
grep '"op":"commit' ~/.cache/things-sync/ops.jsonl | tail -50
```

Set `THINGS_SYNC_LOG=1` to also mirror lines to stderr — handy for
live debugging without watching a second terminal.

`ThingsDB` reads (read-only Things' own SQLite) are intentionally
not logged: pure local queries, no risk surface, and they'd flood
the file.

## Known AppleScript limits

- AS refuses `missing value` for date-typed properties — use
  `update_todo(id, due_date=None)` (Cloud) or `clear_due_date(id)`.
- AS has no heading API — headings live entirely on Cloud (`create_heading`
  / `trash_heading` / `cloud.edit(id, title=…)`).
- `lists` and `todos_in_list` route through the DB because the AS
  `lists` collection is broken on Things 3.22.11.

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
  things.py       — Things class (façade + AS-only ops)
  _db.py          — ThingsDB (read-only SQLite, Things' own DB)
  _cloud.py       — ThingsCloud (HTTP read+write client)
  _mirror.py      — ThingsMirror (cloud-direct local SQLite cache)
  _osascript.py   — osascript runner + delimited-output parser
  _scripts.py     — AppleScript prelude + per-class serializers
```
