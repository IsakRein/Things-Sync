# things-sync

AppleScript-backed Python wrapper around Things 3. Full CRUD over to-dos,
projects, areas, tags, and contacts via the Things 3 scripting dictionary —
no Cloud HTTP, no reverse-engineered protocols.

```python
from things_sync import Things

t = Things()
inbox = t.todos_in_list("Inbox")
todo = t.create_todo("Plan trip", notes="flights + hotel", deadline="2026-05-01", tags=["travel"])
t.complete(todo.id)
t.delete_immediately(todo.id)
```

See `src/things_sync/things.py` for the full method surface.
