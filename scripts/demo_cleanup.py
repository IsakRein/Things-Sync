"""Hard-delete every entity created by `demo.py` (matched by `Demo — ` prefix)."""
from __future__ import annotations

from things_sync import Things


PREFIX = "Demo — "


def main() -> None:
    t = Things()
    ids: list[str] = []
    for x in t.todos():
        if x.name.startswith(PREFIX):
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

    print(f"Deleting {len(ids)} demo entities...")
    for i in ids:
        try:
            t.delete(i)
        except Exception as e:  # noqa: BLE001
            print(f"  skip {i}: {e}")
    t.empty_trash()
    print("Trash emptied.")


if __name__ == "__main__":
    main()
