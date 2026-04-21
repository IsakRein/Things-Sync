# things-sync

A general-purpose Things 3 wrapper: CRUD for projects, tasks, areas, and tags.

- **Reads** go through the local SQLite database at
  `~/Library/Group Containers/JLMPQHK86H.com.culturedcode.ThingsMac/ThingsData-*/Things Database.thingsdatabase/main.sqlite`.
  Opened in read-only mode — safe to run while Things.app is open.
- **Writes** go through the Things Cloud HTTP protocol (`cloud.culturedcode.com`).
  This is reverse-engineered and undocumented; schema may shift on Things releases.

Installed via uv:

```bash
uv tool install -e ~/Projects/Things-Sync
```

## CLI

```
things inbox                       # Show inbox to-dos
things today                       # Today's to-dos (Today + overdue scheduled)
things upcoming [--days N]         # Upcoming within N days
things anytime                     # Anytime list
things someday                     # Someday list
things logbook [--days N]          # Recently completed / canceled
things projects                    # List projects with action counts
things project <id-or-prefix>      # Tasks under a project
things areas                       # Areas
things tags                        # Tags
things search "query"              # Search open tasks
things show <uuid-or-prefix>       # Full detail for one item
things summary                     # Account overview

# Writes (go through Things Cloud)
things add "Title" [--project <id> --area <id> --when today --deadline 2026-05-01 --notes "..."]
things add-project "Title" [--area <id> --notes "..." --deadline 2026-05-01]
things complete <uuid>             # Mark complete
things cancel <uuid>               # Mark cancelled
things reopen <uuid>               # Reopen a completed/cancelled to-do
things trash <uuid>                # Trash
things update <uuid> [...]         # Edit fields

things open <uuid>                 # Open in Things.app via the things:// URL scheme
things doctor                      # Verify SQLite + Cloud credentials
things pull                        # Touch the Cloud to refresh state cache

# App Intents back door (things the Cloud API can't do, e.g. hard delete)
things intents list                # Show all 15 Things App Intents
things shortcut setup              # Print the one-time wrapper-shortcut recipe
things shortcut list [--things]    # List installed shortcuts
things shortcut run <name>         # Invoke a shortcut headlessly
things trash-hard <uuid>           # Permanently delete via `ts-delete` wrapper
```

## App Intents back door

Things exposes 15 App Intents (`things intents list`) — including
`TAIDeleteItems` with `deleteImmediately=true`, which the Cloud protocol
has no equivalent for. We can't call App Intents directly from Python
(AMFI blocks `com.apple.shortcuts.background-running` for non-Apple
binaries), so the tool shells out to `/usr/bin/shortcuts run` against
user-authored wrapper Shortcuts. Build them once via Shortcuts.app —
`things shortcut setup` prints the recipe. Convention: wrappers are
named with the `ts-` prefix.

## Auth

Cloud writes need credentials (your Things Cloud email + password):

```bash
export THINGS_EMAIL='you@example.com'
export THINGS_PASSWORD='your-password'
```

Recommended: put them in `~/.envrc` (direnv) or a gitignored shell secrets file
sourced from `~/.zshenv`, `chmod 600`.

## Python API

```python
from things_sync import ThingsDB
from things_sync.cloud import ThingsCloud

# Read
with ThingsDB() as db:
    for p in db.projects():
        print(p.title, p.uuid)
        for t in db.tasks_for_project(p.uuid):
            print("  ", t.title, t.status)

# Write
with ThingsCloud.from_env() as cc:
    cc.add_project("Plan next trip", area_uuid="...")
    cc.add_task("Book flights", project_uuid="...", when="today", deadline="2026-05-01")
    cc.complete_task("<uuid>")
```

## Protocol notes

The Cloud protocol overview lives in `things_sync/cloud.py`. Key entities:

- `Task6` — used for both to-dos and projects; `tp=0` is a task, `tp=1` is a project.
- `Area3` — areas.
- `ChecklistItem3` — checklist items under a Task6.
- `Tag4` — tags.

Every payload uses 2-3 letter field aliases (`tt=title`, `ss=status`, `sr=scheduled`, etc.); see the `TodoPayload` section in `cloud.py` for the full mapping, captured from live Things.app commits via mitmproxy.

**Known limitation:** `add_recurring_todo` exists but is marked EXPERIMENTAL/BROKEN — the `rr` recurrence-rule payload differs subtly from Things.app's own shape in ways that crash Things.app during history replay. Use the per-occurrence-todo pattern (generate one to-do per cycle, push each as a plain todo) until the full `rr` shape is pinned down via more capture rounds.

## License

MIT.
