"""Rich CLI for Things 3.

Reads via local SQLite (read-only). Writes via Things Cloud directly.
"""

from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path

import typer
from rich.box import SIMPLE_HEAVY
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import cloud
from .db import DEFAULT_DB_PATH, GROUP_CONTAINER, ThingsDB
from .models import STATUS_COMPLETED, Task

app = typer.Typer(
    name="things",
    help="Rich CLI for Things 3 — reads via SQLite, writes via Things Cloud.",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
)

console = Console()
err = Console(stderr=True)


# ---------- rendering helpers ----------

TAG_STYLE = "cyan"
DEADLINE_STYLE = "red"
START_STYLE = "yellow"
PROJECT_STYLE = "magenta"


def _fmt_date(d: date | None) -> str:
    if d is None:
        return ""
    today = date.today()
    delta = (d - today).days
    if delta == 0:
        return "today"
    if delta == 1:
        return "tomorrow"
    if delta == -1:
        return "yesterday"
    if 1 < delta <= 7:
        return d.strftime("%a")
    return d.isoformat()


def _fmt_datetime(dt: datetime | None) -> str:
    if dt is None:
        return ""
    return dt.strftime("%Y-%m-%d %H:%M")


def _short_uuid(uuid: str, n: int = 8) -> str:
    return uuid[:n]


def _task_title(task: Task) -> Text:
    text = Text(task.title or "(untitled)")
    if task.checklist_total:
        text.append(f"  [{task.checklist_total - task.checklist_open}/{task.checklist_total}]", style="dim")
    if task.status == STATUS_COMPLETED:
        text.stylize("strike dim")
    return text


def _tags_text(task: Task) -> Text:
    if not task.tags:
        return Text("")
    return Text(" ".join(f"#{t}" for t in task.tags), style=TAG_STYLE)


def _context_text(task: Task, project_titles: dict[str, str], area_titles: dict[str, str]) -> Text:
    if task.project and task.project in project_titles:
        return Text(project_titles[task.project], style=PROJECT_STYLE)
    if task.area and task.area in area_titles:
        return Text(area_titles[task.area], style="blue")
    return Text("")


def _render_tasks(
    tasks: list[Task],
    *,
    title: str,
    project_titles: dict[str, str],
    area_titles: dict[str, str],
    show_when: bool = True,
) -> None:
    if not tasks:
        console.print(f"[dim]{title}: empty[/dim]")
        return
    table = Table(
        title=f"[bold]{title}[/bold]  [dim]({len(tasks)})[/dim]",
        title_justify="left",
        box=SIMPLE_HEAVY,
        header_style="bold",
        pad_edge=False,
        expand=True,
    )
    table.add_column("id", style="dim", no_wrap=True)
    table.add_column("title", ratio=4)
    table.add_column("context", ratio=2)
    if show_when:
        table.add_column("when", style=START_STYLE, no_wrap=True)
    table.add_column("due", style=DEADLINE_STYLE, no_wrap=True)
    table.add_column("tags", no_wrap=False)
    for t in tasks:
        row = [
            _short_uuid(t.uuid),
            _task_title(t),
            _context_text(t, project_titles, area_titles),
        ]
        if show_when:
            row.append(_fmt_date(t.start_date))
        row.append(_fmt_date(t.deadline))
        row.append(_tags_text(t))
        table.add_row(*row)
    console.print(table)


# ---------- commands ----------


def _resolve_db(path: Path | None) -> ThingsDB:
    return ThingsDB(path)


DBPathOpt = typer.Option(
    None,
    "--db",
    help="Override path to main.sqlite (default: auto-discovered).",
    envvar="THINGS_DB",
)


@app.command()
def inbox(db: Path | None = DBPathOpt) -> None:
    """Show the Inbox."""
    with _resolve_db(db) as conn:
        _render_tasks(
            conn.inbox(),
            title="Inbox",
            project_titles=conn.project_titles(),
            area_titles=conn.area_titles(),
            show_when=False,
        )


@app.command()
def today(db: Path | None = DBPathOpt) -> None:
    """Tasks planned for today (Today + overdue scheduled)."""
    with _resolve_db(db) as conn:
        _render_tasks(
            conn.today(),
            title="Today",
            project_titles=conn.project_titles(),
            area_titles=conn.area_titles(),
        )


@app.command()
def upcoming(
    days: int = typer.Option(30, "--days", "-d", help="Horizon in days."),
    db: Path | None = DBPathOpt,
) -> None:
    """Tasks scheduled within the next N days."""
    with _resolve_db(db) as conn:
        _render_tasks(
            conn.upcoming(days),
            title=f"Upcoming ({days}d)",
            project_titles=conn.project_titles(),
            area_titles=conn.area_titles(),
        )


@app.command()
def anytime(db: Path | None = DBPathOpt) -> None:
    """Tasks in the Anytime list."""
    with _resolve_db(db) as conn:
        _render_tasks(
            conn.anytime(),
            title="Anytime",
            project_titles=conn.project_titles(),
            area_titles=conn.area_titles(),
        )


@app.command()
def someday(db: Path | None = DBPathOpt) -> None:
    """Tasks in the Someday list."""
    with _resolve_db(db) as conn:
        _render_tasks(
            conn.someday(),
            title="Someday",
            project_titles=conn.project_titles(),
            area_titles=conn.area_titles(),
            show_when=False,
        )


@app.command()
def logbook(
    limit: int = typer.Option(50, "--limit", "-n", help="Max rows."),
    db: Path | None = DBPathOpt,
) -> None:
    """Recently completed or canceled tasks."""
    with _resolve_db(db) as conn:
        tasks = conn.logbook(limit=limit)
        if not tasks:
            console.print("[dim]Logbook is empty.[/dim]")
            return
        project_titles = conn.project_titles()
        area_titles = conn.area_titles()
        table = Table(
            title=f"[bold]Logbook[/bold]  [dim](last {len(tasks)})[/dim]",
            title_justify="left",
            box=SIMPLE_HEAVY,
            header_style="bold",
            expand=True,
        )
        table.add_column("id", style="dim", no_wrap=True)
        table.add_column("title", ratio=4)
        table.add_column("context", ratio=2)
        table.add_column("status", no_wrap=True)
        table.add_column("stopped", style="dim", no_wrap=True)
        for t in tasks:
            table.add_row(
                _short_uuid(t.uuid),
                _task_title(t),
                _context_text(t, project_titles, area_titles),
                t.status_label,
                _fmt_datetime(t.stop_date),
            )
        console.print(table)


@app.command()
def projects(
    include_completed: bool = typer.Option(False, "--all", help="Include completed."),
    db: Path | None = DBPathOpt,
) -> None:
    """List projects with action counts."""
    with _resolve_db(db) as conn:
        project_rows = conn.projects(include_completed=include_completed)
        area_titles = conn.area_titles()
        if not project_rows:
            console.print("[dim]No projects.[/dim]")
            return
        table = Table(
            title=f"[bold]Projects[/bold]  [dim]({len(project_rows)})[/dim]",
            title_justify="left",
            box=SIMPLE_HEAVY,
            header_style="bold",
            expand=True,
        )
        table.add_column("id", style="dim", no_wrap=True)
        table.add_column("title", ratio=3)
        table.add_column("area", style="blue", ratio=2)
        table.add_column("open", justify="right")
        table.add_column("total", justify="right", style="dim")
        table.add_column("deadline", style=DEADLINE_STYLE, no_wrap=True)
        for p in project_rows:
            table.add_row(
                _short_uuid(p.uuid),
                p.title,
                area_titles.get(p.area or "", ""),
                str(p.open_actions),
                str(p.total_actions),
                _fmt_date(p.deadline),
            )
        console.print(table)


@app.command()
def project(
    name_or_id: str = typer.Argument(..., help="Project title (case-insensitive) or uuid prefix."),
    include_completed: bool = typer.Option(False, "--all", help="Include completed tasks."),
    db: Path | None = DBPathOpt,
) -> None:
    """Show tasks under a single project."""
    with _resolve_db(db) as conn:
        candidates = conn.projects(include_completed=True)
        match = _pick_project(candidates, name_or_id)
        if match is None:
            err.print(f"[red]No project matching[/red] {name_or_id!r}")
            raise typer.Exit(1)
        tasks = conn.tasks_for_project(match.uuid, include_completed=include_completed)
        area_titles = conn.area_titles()
        header = f"{match.title}"
        if match.area and match.area in area_titles:
            header += f"   [blue]{area_titles[match.area]}[/blue]"
        console.print(Panel(header, style="bold magenta", expand=False))
        _render_tasks(
            tasks,
            title=f"{len(tasks)} tasks",
            project_titles={match.uuid: match.title},
            area_titles=area_titles,
        )


def _pick_project(candidates, name_or_id: str):
    needle = name_or_id.lower()
    for p in candidates:
        if p.uuid == name_or_id or p.uuid.startswith(name_or_id):
            return p
    for p in candidates:
        if (p.title or "").lower() == needle:
            return p
    for p in candidates:
        if needle in (p.title or "").lower():
            return p
    return None


@app.command()
def areas(db: Path | None = DBPathOpt) -> None:
    """List areas."""
    with _resolve_db(db) as conn:
        items = conn.areas()
    table = Table(title="[bold]Areas[/bold]", title_justify="left", box=SIMPLE_HEAVY, header_style="bold")
    table.add_column("id", style="dim", no_wrap=True)
    table.add_column("title")
    table.add_column("visible", style="dim")
    for a in items:
        table.add_row(_short_uuid(a.uuid), a.title, "yes" if a.visible else "no")
    console.print(table)


@app.command()
def tags(db: Path | None = DBPathOpt) -> None:
    """List tags."""
    with _resolve_db(db) as conn:
        items = conn.tags()
    table = Table(title="[bold]Tags[/bold]", title_justify="left", box=SIMPLE_HEAVY, header_style="bold")
    table.add_column("id", style="dim", no_wrap=True)
    table.add_column("title", style=TAG_STYLE)
    table.add_column("shortcut", style="dim")
    for t in items:
        table.add_row(_short_uuid(t.uuid), t.title, t.shortcut or "")
    console.print(table)


@app.command()
def search(
    query: str = typer.Argument(..., help="Substring to match in title or notes."),
    include_completed: bool = typer.Option(False, "--all", help="Include completed."),
    limit: int = typer.Option(50, "--limit", "-n"),
    db: Path | None = DBPathOpt,
) -> None:
    """Search open tasks by title or notes."""
    with _resolve_db(db) as conn:
        results = conn.search(query, include_completed=include_completed, limit=limit)
        _render_tasks(
            results,
            title=f"Search: {query!r}",
            project_titles=conn.project_titles(),
            area_titles=conn.area_titles(),
        )


@app.command()
def show(
    uuid: str = typer.Argument(..., help="Task uuid (full or prefix of length ≥4)."),
    db: Path | None = DBPathOpt,
    open_in_things: bool = typer.Option(
        False, "--open", help="Also reveal this task in Things.app."
    ),
) -> None:
    """Show one task in detail."""
    with _resolve_db(db) as conn:
        task = _resolve_task(conn, uuid)
        if task is None:
            err.print(f"[red]No task matching[/red] {uuid!r}")
            raise typer.Exit(1)
        project_titles = conn.project_titles()
        area_titles = conn.area_titles()

    body = Text()
    body.append("uuid:     ", style="dim")
    body.append(task.uuid + "\n")
    body.append("status:   ", style="dim")
    body.append(task.status_label + "\n")
    body.append("start:    ", style="dim")
    body.append(task.start_label + "\n")
    if task.start_date:
        body.append("when:     ", style="dim")
        body.append(task.start_date.isoformat() + "\n")
    if task.deadline:
        body.append("deadline: ", style="dim")
        body.append(task.deadline.isoformat() + "\n", style=DEADLINE_STYLE)
    if task.project and task.project in project_titles:
        body.append("project:  ", style="dim")
        body.append(project_titles[task.project] + "\n", style=PROJECT_STYLE)
    if task.area and task.area in area_titles:
        body.append("area:     ", style="dim")
        body.append(area_titles[task.area] + "\n", style="blue")
    if task.tags:
        body.append("tags:     ", style="dim")
        body.append(" ".join(f"#{t}" for t in task.tags) + "\n", style=TAG_STYLE)
    if task.creation_date:
        body.append("created:  ", style="dim")
        body.append(_fmt_datetime(task.creation_date) + "\n")
    if task.modification_date:
        body.append("modified: ", style="dim")
        body.append(_fmt_datetime(task.modification_date) + "\n")
    if task.notes:
        body.append("\n")
        body.append(task.notes)
    console.print(Panel(body, title=task.title or "(untitled)", border_style="magenta"))

    if open_in_things:
        import subprocess
        subprocess.run(["open", "-g", f"things:///show?id={task.uuid}"], check=False)


def _resolve_task(conn: ThingsDB, uuid: str) -> Task | None:
    if len(uuid) >= 20:
        return conn.get(uuid)
    if len(uuid) < 4:
        raise typer.BadParameter("Provide at least 4 chars of the uuid prefix.")
    # scan: prefix match on open tasks across all containers
    rows = conn.conn.execute(
        "SELECT uuid FROM TMTask WHERE trashed=0 AND uuid LIKE ? LIMIT 2",
        (f"{uuid}%",),
    ).fetchall()
    if not rows:
        return None
    if len(rows) > 1:
        err.print(f"[yellow]Ambiguous prefix {uuid!r} — {len(rows)}+ matches. Use more chars.[/yellow]")
        return None
    return conn.get(rows[0]["uuid"])


@app.command()
def summary(db: Path | None = DBPathOpt) -> None:
    """Bucket counts across Inbox/Today/Anytime/Someday/Projects."""
    with _resolve_db(db) as conn:
        s = conn.summary()
    table = Table(box=SIMPLE_HEAVY, show_header=False)
    table.add_column("bucket", style="bold")
    table.add_column("count", justify="right")
    for key in ("inbox", "today", "anytime", "someday", "projects"):
        table.add_row(key, str(s[key]))
    console.print(Panel(table, title="Things summary", border_style="cyan", expand=False))


def _cloud() -> cloud.ThingsCloud:
    try:
        return cloud.ThingsCloud.from_env()
    except cloud.CloudAuthError as exc:
        err.print(f"[red]Auth failed:[/red] {exc}")
        raise typer.Exit(2)
    except RuntimeError as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(2)


def _resolve_project_uuid(name_or_id: str | None) -> str | None:
    if not name_or_id:
        return None
    with ThingsDB() as conn:
        projects = conn.projects(include_completed=False)
    needle = name_or_id.lower()
    for p in projects:
        if p.uuid == name_or_id or p.uuid.startswith(name_or_id):
            return p.uuid
    for p in projects:
        if (p.title or "").lower() == needle:
            return p.uuid
    for p in projects:
        if needle in (p.title or "").lower():
            return p.uuid
    err.print(f"[red]No project matching[/red] {name_or_id!r}")
    raise typer.Exit(1)


def _resolve_area_uuid(name_or_id: str | None) -> str | None:
    if not name_or_id:
        return None
    with ThingsDB() as conn:
        areas_ = conn.areas()
    needle = name_or_id.lower()
    for a in areas_:
        if a.uuid == name_or_id or a.uuid.startswith(name_or_id):
            return a.uuid
    for a in areas_:
        if (a.title or "").lower() == needle:
            return a.uuid
    for a in areas_:
        if needle in (a.title or "").lower():
            return a.uuid
    err.print(f"[red]No area matching[/red] {name_or_id!r}")
    raise typer.Exit(1)


@app.command()
def add(
    title: str = typer.Argument(..., help="To-do title."),
    notes: str = typer.Option("", "--notes", "-N", help="Notes body."),
    when: str = typer.Option("", "--when", "-w", help="today | tomorrow | YYYY-MM-DD"),
    deadline: str = typer.Option("", "--deadline", "-d", help="YYYY-MM-DD"),
    project: str = typer.Option("", "--project", "-p", help="Project name or uuid prefix."),
    area: str = typer.Option("", "--area", "-a", help="Area name or uuid prefix."),
) -> None:
    """Create a new to-do via Things Cloud."""
    if project and area:
        err.print("[red]--project and --area are mutually exclusive[/red]")
        raise typer.Exit(1)
    project_uuid = _resolve_project_uuid(project or None)
    area_uuid = _resolve_area_uuid(area or None)
    with _cloud() as cc:
        uuid = cc.add_task(
            title,
            notes=notes,
            when=when or None,
            deadline=deadline or None,
            project_uuid=project_uuid,
            area_uuid=area_uuid,
        )
    console.print(f"[green]+[/green] {title}  [dim]{uuid}[/dim]")


@app.command("add-project")
def add_project(
    title: str = typer.Argument(...),
    notes: str = typer.Option("", "--notes", "-N"),
    area: str = typer.Option("", "--area", "-a"),
    deadline: str = typer.Option("", "--deadline", "-d"),
) -> None:
    """Create a new project via Things Cloud."""
    area_uuid = _resolve_area_uuid(area or None)
    with _cloud() as cc:
        uuid = cc.add_project(title, notes=notes, area_uuid=area_uuid, deadline=deadline or None)
    console.print(f"[magenta]+[/magenta] project {title}  [dim]{uuid}[/dim]")


@app.command()
def complete(uuid: str = typer.Argument(..., help="Task uuid or prefix.")) -> None:
    """Mark a task as completed via Things Cloud."""
    full = _resolve_full_uuid(uuid)
    with _cloud() as cc:
        cc.complete_task(full)
    console.print(f"[green]✓[/green] completed {full}")


@app.command()
def cancel(uuid: str = typer.Argument(...)) -> None:
    """Mark a task as canceled via Things Cloud."""
    full = _resolve_full_uuid(uuid)
    with _cloud() as cc:
        cc.cancel_task(full)
    console.print(f"[yellow]x[/yellow] canceled {full}")


@app.command()
def reopen(uuid: str = typer.Argument(...)) -> None:
    """Re-open a completed/canceled task."""
    full = _resolve_full_uuid(uuid)
    with _cloud() as cc:
        cc.reopen_task(full)
    console.print(f"[cyan]↺[/cyan] reopened {full}")


@app.command()
def trash(uuid: str = typer.Argument(...)) -> None:
    """Move a task to the trash via Things Cloud."""
    full = _resolve_full_uuid(uuid)
    with _cloud() as cc:
        cc.trash_task(full)
    console.print(f"[red]🗑[/red] trashed {full}")


@app.command()
def update(
    uuid: str = typer.Argument(...),
    title: str = typer.Option("", "--title"),
    notes: str = typer.Option("", "--notes"),
    when: str = typer.Option("__UNSET__", "--when", help="today | tomorrow | YYYY-MM-DD | '' to clear"),
    deadline: str = typer.Option("__UNSET__", "--deadline", help="YYYY-MM-DD | '' to clear"),
) -> None:
    """Edit a task's fields via Things Cloud."""
    full = _resolve_full_uuid(uuid)
    kwargs: dict = {}
    if title:
        kwargs["title"] = title
    if notes:
        kwargs["notes"] = notes
    if when != "__UNSET__":
        kwargs["when"] = when or None
        kwargs["when_set"] = True
    if deadline != "__UNSET__":
        kwargs["deadline"] = deadline or None
        kwargs["deadline_set"] = True
    if not kwargs:
        err.print("[red]No fields to update.[/red]")
        raise typer.Exit(1)
    with _cloud() as cc:
        cc.edit_task(full, **kwargs)
    console.print(f"[cyan]~[/cyan] updated {full}")


def _resolve_full_uuid(prefix: str) -> str:
    if len(prefix) >= 20:
        return prefix
    with ThingsDB() as conn:
        task = _resolve_task(conn, prefix)
        if task is None:
            err.print(f"[red]No task matching[/red] {prefix!r}")
            raise typer.Exit(1)
        return task.uuid


@app.command("open")
def open_cmd(uuid: str = typer.Argument(..., help="Task/project/area/tag uuid or prefix.")) -> None:
    """Reveal a task in the Things.app UI."""
    import subprocess
    full = _resolve_full_uuid(uuid)
    subprocess.run(["open", "-g", f"things:///show?id={full}"], check=False)
    console.print(f"[cyan]→[/cyan] opened {full} in Things")


@app.command()
def pull(
    start_index: int = typer.Option(-1, "--from", help="Override start-index (default: saved head)."),
) -> None:
    """Pull history from Things Cloud since last known head-index.

    Things 3 on this Mac syncs independently — a `pull` here just fetches
    the raw delta so you can verify connectivity. Use `inbox`/`today`/etc.
    (SQLite) to read the current state after Things.app has re-synced.
    """
    with _cloud() as cc:
        idx = start_index if start_index >= 0 else cc.state.head_index
        data = cc.fetch(idx)
        items = data.get("items", [])
        new_head = int(data.get("current-item-index", idx))
        cc.state.head_index = new_head
        cc.state.save()
    console.print(
        f"[green]pulled[/green] {len(items)} items  "
        f"[dim]start={idx} → head={new_head}[/dim]"
    )


@app.command()
def doctor() -> None:
    """Verify local Things 3 install + Things Cloud auth."""
    ok_all = True

    def line(ok: bool, label: str, detail: str = "") -> None:
        nonlocal ok_all
        if not ok:
            ok_all = False
        tag = "[green][OK][/green]  " if ok else "[red][FAIL][/red]"
        console.print(f"  {tag} {label}")
        if detail:
            console.print(f"          [dim]{detail}[/dim]")

    console.rule("[bold]Things 3 doctor")

    app_path = Path("/Applications/Things3.app")
    line(app_path.exists(), "Things3.app installed", str(app_path))
    line(GROUP_CONTAINER.exists(), "Group container present", str(GROUP_CONTAINER))

    if DEFAULT_DB_PATH and DEFAULT_DB_PATH.exists():
        line(True, "Things database located", str(DEFAULT_DB_PATH))
        try:
            with ThingsDB(DEFAULT_DB_PATH) as db:
                s = db.summary()
            line(
                True,
                "Database readable",
                f"{s['inbox']} inbox · {s['anytime']} anytime · {s['projects']} projects",
            )
        except Exception as exc:
            line(False, "Database readable", repr(exc))
    else:
        line(False, "Things database located", "set THINGS_DB to override")

    # Cloud auth
    if not (os.environ.get("THINGS_EMAIL") and os.environ.get("THINGS_PASSWORD")):
        line(False, "THINGS_EMAIL + THINGS_PASSWORD set", "needed for cloud writes")
    else:
        try:
            with cloud.ThingsCloud.from_env() as cc:
                line(
                    True,
                    "Things Cloud login",
                    f"history-key={cc.account.info.history_key}",
                )
                line(
                    True,
                    "Current head index",
                    f"{cc.state.head_index}",
                )
        except cloud.CloudAuthError as exc:
            line(False, "Things Cloud login", str(exc))
        except Exception as exc:
            line(False, "Things Cloud login", repr(exc))

    console.rule(
        "[bold green]OK[/bold green]" if ok_all else "[bold red]FAIL[/bold red]"
    )
    raise typer.Exit(0 if ok_all else 1)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
