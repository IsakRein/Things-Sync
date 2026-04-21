"""Second demo — exercises Cloud sync surface across more dimensions.

Different prefix (`Demo2 — `) so it doesn't collide with `demo.py`.
Both `demo_cleanup.py` and `demo2_cleanup.py` are name-prefix scoped.
"""
from __future__ import annotations

import time
from datetime import date, timedelta

from things_sync import Things


PREFIX = "Demo2 — "


def main() -> None:
    t = Things()
    print(f"Things {t.version()}\n")

    # --- Tag tree (3 levels deep) ---
    health = t.create_tag(f"{PREFIX}health", shortcut="h")
    fitness = t.create_tag(f"{PREFIX}fitness", parent=health.name)
    cardio = t.create_tag(f"{PREFIX}cardio", parent=fitness.name)
    strength = t.create_tag(f"{PREFIX}strength", parent=fitness.name)
    nutrition = t.create_tag(f"{PREFIX}nutrition", parent=health.name)
    print(f"3-level tag tree: health → (fitness → (cardio, strength), nutrition)")

    # --- Areas with reordered/multi tags ---
    home = t.create_area(f"{PREFIX}Home", tags=[health.name])
    learning = t.create_area(f"{PREFIX}Learning")
    print(f"Areas: {home.name}, {learning.name}")

    # --- Project with all-the-bells ---
    marathon = t.create_project(
        f"{PREFIX}Marathon training 🏃",
        notes=(
            "Goal: sub-4 by October.\n"
            "\n"
            "Phases:\n"
            "  1. Base (8wk)\n"
            "  2. Build (6wk)\n"
            "  3. Peak (4wk)\n"
            "  4. Taper (2wk)\n"
            "\n"
            "Reference plan: https://example.com/sub4-plan"
        ),
        deadline=date.today() + timedelta(days=180),
        tags=[fitness.name, cardio.name],
        area=home.id,
    )
    course = t.create_project(
        f"{PREFIX}Learn German A2 — Deutsch lernen",
        notes="Goethe-Institut A2 cert by year-end.\nPractice: 30min/day on Anki + Tandem.",
        deadline=date.today() + timedelta(days=90),
        area=learning.id,
    )
    print(f"Projects: {marathon.name} (Home), {course.name} (Learning)")

    # --- Todos that land in different built-in lists via schedule ---
    today_run = t.create_todo(f"{PREFIX}Easy run 5km", project=marathon.id, tags=[cardio.name])
    t.schedule(today_run.id, date.today())

    tomorrow_strength = t.create_todo(f"{PREFIX}Squats 3×8", project=marathon.id, tags=[strength.name])
    t.schedule(tomorrow_strength.id, date.today() + timedelta(days=1))

    next_week_long = t.create_todo(
        f"{PREFIX}Long run 18km",
        project=marathon.id,
        tags=[cardio.name],
        deadline=date.today() + timedelta(days=7),
    )
    t.schedule(next_week_long.id, date.today() + timedelta(days=7))

    next_month_race = t.create_todo(
        f"{PREFIX}Race-pace tempo 8km",
        project=marathon.id,
        tags=[cardio.name],
    )
    t.schedule(next_month_race.id, date.today() + timedelta(days=30))
    print(f"Scheduled marathon todos: today / tomorrow / +7d / +30d")

    # --- Someday list ---
    someday1 = t.create_todo(f"{PREFIX}Run an ultra someday", project=marathon.id, tags=[cardio.name])
    t.move_to_list(someday1.id, "Someday")
    someday2 = t.create_todo(f"{PREFIX}Read 'Born to Run'", tags=[health.name])
    t.move_to_list(someday2.id, "Someday")
    print(f"Moved 2 todos to Someday")

    # --- Anytime list (default for projects-without-schedule) ---
    anytime = t.create_todo(f"{PREFIX}Replace running shoes", tags=[fitness.name])
    t.move_to_list(anytime.id, "Anytime")
    print(f"Moved 1 todo to Anytime")

    # --- German course todos ---
    grammar = t.create_todo(
        f"{PREFIX}Modal verbs review",
        notes="können, müssen, dürfen, sollen, wollen, mögen + möchten",
        project=course.id,
    )
    vocab = t.create_todo(f"{PREFIX}Anki — 50 new cards", project=course.id)
    speaking = t.create_todo(
        f"{PREFIX}Tandem session — 30min",
        project=course.id,
        deadline=date.today() + timedelta(days=2),
    )
    print(f"German course: 3 todos")

    # --- Special characters / unicode stress test ---
    unicode_todo = t.create_todo(
        f"{PREFIX}日本語 + Ελληνικά + עברית + emoji 🎉🌮 — sync me",
        notes="Quotes inside: 'single' \"double\".\nBackslash \\ test.\nAmpersand & test.\nLong ——— em dashes.",
    )
    print(f"Unicode todo: {unicode_todo.name!r}")

    # --- Long notes ---
    long_notes = t.create_todo(
        f"{PREFIX}Long-notes test",
        notes=("\n".join(f"Line {i}: lorem ipsum dolor sit amet, consectetur adipiscing elit." for i in range(40))),
    )
    print(f"Long-notes todo: {long_notes.name!r} ({len(long_notes.notes)} chars)")

    # --- Status flow: create, complete, then immediately reopen (Cloud should reflect both) ---
    flow = t.create_todo(f"{PREFIX}Status round-trip — should end OPEN")
    t.complete(flow.id)
    time.sleep(0.5)
    t.reopen(flow.id)
    final = t.todo(flow.id)
    print(f"Round-trip: completed → reopened, final status = {final.status.value if final else '?'}")

    # --- Tag reassignment ---
    multi_tag = t.create_todo(f"{PREFIX}Multi-tagged todo", tags=[cardio.name, strength.name, fitness.name])
    print(f"Multi-tagged: {multi_tag.tag_names}")

    # --- Update an existing tag's keyboard shortcut + parent ---
    t.update_tag(nutrition.id, shortcut="n")
    print(f"Updated tag {nutrition.name!r} shortcut → 'n'")

    # --- Rename a project mid-flight ---
    renamed = t.update_project(course.id, name=f"{PREFIX}Learn German A2 (renamed)")
    print(f"Renamed project: {renamed.name!r}")

    # --- Soft-delete chain: create, delete (→ Trash), then user can verify it shows in Trash ---
    trashable = t.create_todo(f"{PREFIX}Should appear in Trash on every device")
    t.delete(trashable.id)
    print(f"Soft-deleted (in Trash): {trashable.name!r}")

    # --- Counts ---
    print()
    print("Final counts —")
    print(f"  todos: {t.count_todos()}")
    print(f"  projects: {t.count_projects()}")
    print(f"  areas: {t.count_areas()}")
    print(f"  tags: {t.count_tags()}")
    print()
    print("Watch your other Things devices (iOS / iPad / web) for sync of:")
    print("  • Areas: Demo2 — Home, Demo2 — Learning")
    print("  • Projects: Marathon training 🏃, Learn German A2 (renamed)")
    print("  • Today: easy run + scheduled items")
    print("  • Someday: 2 items")
    print("  • Anytime: 1 item")
    print("  • Tags settings: 3-level health → fitness → (cardio, strength) tree, nutrition shortcut 'n'")
    print("  • Trash: 'Should appear in Trash on every device'")
    print()
    print("Cleanup: `uv run python scripts/demo2_cleanup.py` (also wipes the Trash item)")


if __name__ == "__main__":
    main()
