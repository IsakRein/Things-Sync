"""Populate Things 3 with a variety of entities to showcase the wrapper API.

Everything created here is name-prefixed `Demo — ` so it's easy to spot in
the sidebar and clean up afterwards (run `scripts/demo_cleanup.py`).
"""
from __future__ import annotations

from datetime import date, timedelta

from things_sync import Status, Things


PREFIX = "Demo — "


def main() -> None:
    t = Things()
    print(f"Things {t.version()}")
    print()

    # --- Tags (with hierarchy + keyboard shortcut) ---
    travel = t.create_tag(f"{PREFIX}travel", shortcut="t")
    flights = t.create_tag(f"{PREFIX}flights", parent=travel.name)
    hotels = t.create_tag(f"{PREFIX}hotels", parent=travel.name)
    urgent = t.create_tag(f"{PREFIX}urgent", shortcut="u")
    print(f"Created tags: {travel.name} (with children {flights.name!r}, {hotels.name!r}), {urgent.name}")

    # --- Areas ---
    work = t.create_area(f"{PREFIX}Work")
    personal = t.create_area(f"{PREFIX}Personal", tags=[travel.name])
    print(f"Created areas: {work.name}, {personal.name} (tagged {travel.name!r})")

    # --- Projects ---
    trip = t.create_project(
        f"{PREFIX}Tokyo trip",
        notes="Two weeks in May.\nLand at Narita; out from Haneda.",
        deadline=date.today() + timedelta(days=30),
        tags=[travel.name],
        area=personal.id,
    )
    launch = t.create_project(
        f"{PREFIX}Q3 launch",
        notes="Cross-functional release. Mobile + web + email.",
        deadline=date.today() + timedelta(days=60),
        area=work.id,
    )
    print(f"Created projects: {trip.name} (in Personal), {launch.name} (in Work)")

    # --- To-dos under the trip project (variety of features) ---
    flight_todo = t.create_todo(
        f"{PREFIX}Book outbound flight",
        notes="Aisle seat. Star Alliance preferred.",
        deadline=date.today() + timedelta(days=7),
        tags=[flights.name, urgent.name],
        project=trip.id,
    )
    hotel_todo = t.create_todo(
        f"{PREFIX}Reserve hotel in Shinjuku",
        notes="Walking distance to Yamanote line.",
        tags=[hotels.name],
        project=trip.id,
    )
    visa_todo = t.create_todo(
        f"{PREFIX}Confirm visa rules",
        project=trip.id,
    )
    # Schedule one for tomorrow
    t.schedule(visa_todo.id, date.today() + timedelta(days=1))
    print(f"Created 3 todos under {trip.name!r}; visa todo scheduled for tomorrow.")

    # --- To-dos under the work project ---
    spec = t.create_todo(
        f"{PREFIX}Write launch spec",
        notes="Doc here:\n  - acceptance criteria\n  - rollout plan\n  - rollback plan",
        deadline=date.today() + timedelta(days=14),
        project=launch.id,
    )
    review = t.create_todo(
        f"{PREFIX}Get spec reviewed",
        project=launch.id,
    )
    ship = t.create_todo(
        f"{PREFIX}Ship to staging",
        deadline=date.today() + timedelta(days=45),
        project=launch.id,
    )
    print(f"Created 3 todos under {launch.name!r}.")

    # --- Standalone to-dos in Inbox ---
    inbox_a = t.create_todo(f"{PREFIX}Buy birthday gift", tags=[urgent.name])
    inbox_b = t.create_todo(f"{PREFIX}Call dentist", notes="Cleaning + check sealant on #14.")
    print(f"Created 2 inbox todos.")

    # --- A todo scheduled for Today ---
    today_todo = t.create_todo(f"{PREFIX}Morning workout — pushups + planks")
    t.schedule(today_todo.id, date.today())
    print(f"Created Today todo: {today_todo.name!r}")

    # --- Status demonstration: complete one, cancel one ---
    completed = t.create_todo(f"{PREFIX}Already done — make coffee")
    t.complete(completed.id)
    canceled = t.create_todo(f"{PREFIX}Skipped — dry-cleaning pickup")
    t.cancel(canceled.id)
    print(f"Marked one todo completed, one canceled.")

    # --- Move demonstration: created in Inbox, then moved into a project ---
    mover = t.create_todo(f"{PREFIX}Started in Inbox, moved to Tokyo trip")
    t.move_to_project(mover.id, trip.id)
    print(f"Created todo in Inbox, moved into {trip.name!r}.")

    # --- Update demonstration ---
    t.update_todo(
        hotel_todo.id,
        notes="UPDATED: looking at Park Hyatt or Granbell.",
        tags=[hotels.name, urgent.name],
    )
    print(f"Updated hotel todo notes + added urgent tag.")

    # --- Quicksilver-syntax create ---
    qs = t.parse_quicksilver(f"{PREFIX}Quicksilver-parsed todo")
    print(f"Created via parse_quicksilver: {qs.name!r}")

    print()
    print("Summary —")
    print(f"  total todos: {t.count_todos()}")
    print(f"  total projects: {t.count_projects()}")
    print(f"  total areas: {t.count_areas()}")
    print(f"  total tags: {t.count_tags()}")
    print()
    print("Look in Things for the 'Demo — ' prefix in the sidebar (Personal/Work areas,")
    print("Tokyo trip / Q3 launch projects), Inbox, Today, Logbook, and Tags settings.")
    print()
    print("To clean up: run `uv run python scripts/demo_cleanup.py`")


if __name__ == "__main__":
    main()
