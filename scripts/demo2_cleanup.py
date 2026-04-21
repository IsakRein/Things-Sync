"""Hard-delete every entity from `demo2.py` (matched by `Demo2 — ` prefix)."""
from __future__ import annotations

from things_sync import Things


PREFIX = "Demo2 — "


def main() -> None:
    t = Things()
    ids: list[str] = []
    # Also reach into Trash — demo2 leaves an item there deliberately.
    for x in list(t.todos()) + list(t.todos_in_list("Trash")):
        if x.name.startswith(PREFIX) and x.id not in ids:
            ids.append(x.id)
    for x in t.projects():
        if x.name.startswith(PREFIX):
            ids.append(x.id)
    for x in t.areas():
        if x.name.startswith(PREFIX):
            ids.append(x.id)
    for x in t.tags():
        if x.name.startswith(PREFIX):
            ids.append(x.id)

    print(f"Deleting {len(ids)} demo2 entities...")
    for i in ids:
        try:
            t.delete(i)
        except Exception as e:  # noqa: BLE001
            print(f"  skip {i}: {e}")
    t.empty_trash()
    print("Trash emptied.")


if __name__ == "__main__":
    main()
