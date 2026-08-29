"""jobhunter CLI (typer).

Checkpoint 1 gives you a working skeleton: `init`, `stats`, `jobs`, `paste`,
`pause`/`resume` are functional against the store; the pipeline verbs
(`run`, `watch`, `apply`, `undo`) print their build-order checkpoint until
their module lands. Safety posture is dry-run + approval by default.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from .config import PAUSED_FILE, PROJECT_ROOT, load_config
from . import store

app = typer.Typer(
    add_completion=False,
    help="Self-hosted personal job-application agent (manual intake, approval-gated).",
    no_args_is_help=True,
)
console = Console()

_CHECKPOINT = {
    "run": "Checkpoints 2-5 (ingest -> parse -> filter -> score -> draft -> dry-run)",
    "watch": "Checkpoint 8 (APScheduler polling loop)",
    "apply": "Checkpoint 5+ (per-channel submission adapters, approval-gated)",
    "undo": "Checkpoint 8 (send log + undo --last)",
}


def _pending(verb: str) -> None:
    console.print(f"[yellow]`{verb}` is not built yet — {_CHECKPOINT[verb]}.[/yellow]")
    console.print("This is Checkpoint 1: scaffold, config, models, CLI skeleton.")


@app.command()
def init() -> None:
    """Scaffold runtime dirs, create the SQLite schema, and check config.

    (OAuth flows + `playwright install` are wired in their later checkpoints.)
    """
    cfg = load_config()
    for d in ["inbox", "outbox", "screenshots", ".auth", "profile"]:
        Path(PROJECT_ROOT / d).mkdir(parents=True, exist_ok=True)
    store.init_db(cfg)
    console.print("[green]Initialized[/green] runtime dirs and SQLite schema.")
    console.print(f"  db            : {cfg.db_url}")
    console.print(f"  active source : {cfg.ingest.active_source}")
    console.print(f"  inbox         : {cfg.ingest.manual.inbox_dir}")
    console.print(
        f"  posture       : dry_run={cfg.submission.dry_run_default}, "
        f"require_approval={cfg.submission.require_approval}"
    )
    if not (PROJECT_ROOT / "profile" / "resume.json").exists():
        console.print(
            "[yellow]Note:[/yellow] profile/resume.json not found — drafting needs it."
        )


@app.command()
def paste(
    text: Optional[str] = typer.Argument(None, help="Raw post text. Omit to read stdin."),
    handle: Optional[str] = typer.Option(None, help="Poster's @handle."),
    url: Optional[str] = typer.Option(None, help="Source URL of the post."),
) -> None:
    """Drop a raw post into the manual-intake inbox for the next ingest cycle."""
    import json
    import sys

    cfg = load_config()
    body = text if text is not None else sys.stdin.read()
    body = (body or "").strip()
    if not body:
        console.print("[red]No post text provided.[/red]")
        raise typer.Exit(1)
    inbox = Path(cfg.ingest.manual.inbox_dir)
    inbox.mkdir(parents=True, exist_ok=True)
    existing = len(list(inbox.glob("paste_*.json")))
    target = inbox / f"paste_{existing + 1:04d}.json"
    target.write_text(
        json.dumps({"content": body, "handle": handle, "url": url}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    console.print(f"[green]Queued[/green] -> {target}")


@app.command()
def ingest() -> None:
    """Pull new posts from the active source, dedupe, and store them.

    Manual-intake source: reads inbox/ (and anything queued via `paste`).
    Already-seen posts are skipped by content hash.
    """
    from .ingest import run_ingest

    cfg = load_config()
    result = run_ingest(cfg)
    console.print(
        f"[green]Ingest[/green] source={cfg.ingest.active_source} — "
        f"fetched {result.fetched}, added [bold]{result.added}[/bold], "
        f"skipped {result.skipped_duplicate} duplicate(s), archived {result.archived}."
    )
    with store.get_session(cfg) as session:
        total = session.query(store.RawPost).count()
    console.print(f"raw_posts total: [bold]{total}[/bold]")


@app.command()
def parse() -> None:
    """Parse un-parsed raw_posts into structured jobs (LLM provider, or offline heuristic)."""
    from .parse import run_parse

    cfg = load_config()
    result = run_parse(cfg)
    degraded = result.get("degraded_to_offline", 0)
    console.print(
        f"[green]Parse[/green] engine={result['engine']} (provider={cfg.llm.provider}) — "
        f"parsed [bold]{result['parsed']}[/bold], "
        f"quarantined {result['quarantined']}"
        + (f", degraded-to-offline {degraded}" if degraded else "") + "."
    )
    if result["engine"] == "heuristic" and cfg.resolved_provider is None:
        key = "GROQ_API_KEY" if cfg.llm.provider in ("groq", "auto") else "ANTHROPIC_API_KEY"
        console.print(
            f"[yellow]No {key}[/yellow] — used the offline heuristic parser (no billing). "
            f"Set the key (and `uv sync --extra groq` for Groq) to parse with the LLM."
        )


@app.command()
def filter() -> None:  # noqa: A001 - CLI verb name mirrors the module
    """Apply hard filter criteria; survivors -> passed_filter, rest -> filtered_out."""
    from .filter import run_filter

    cfg = load_config()
    result = run_filter(cfg)
    total = result["passed"] + result["rejected"]
    rate = (100 * result["passed"] / total) if total else 0.0
    console.print(
        f"[green]Filter[/green] — passed [bold]{result['passed']}[/bold], "
        f"rejected {result['rejected']} ({rate:.0f}% pass rate)."
    )


@app.command()
def score() -> None:
    """Score passed_filter jobs 0-100 (weights from config.yaml) -> scored."""
    from .score import run_score

    cfg = load_config()
    result = run_score(cfg)
    console.print(f"[green]Score[/green] — scored [bold]{result['scored']}[/bold] job(s).")
    if not result["resume_loaded"]:
        console.print("[yellow]profile/resume.json not found[/yellow] — tech-overlap scored 0.")


@app.command()
def draft(limit: int = typer.Option(None, "--limit", "-n", help="Draft only the top N by fit score.")) -> None:
    """Draft tailored applications for scored jobs (LLM provider, or offline template)."""
    from .draft import run_draft

    cfg = load_config()
    result = run_draft(cfg, limit=limit)
    if result.get("error"):
        console.print(f"[red]{result['error']}[/red]")
        raise typer.Exit(1)
    degraded = result.get("degraded_to_offline", 0)
    console.print(
        f"[green]Draft[/green] engine={result['engine']} (provider={cfg.llm.provider}) — "
        f"drafted [bold]{result['drafted']}[/bold], skipped {result['skipped']}"
        + (f", degraded-to-offline {degraded}" if degraded else "")
        + ". Nothing sent (drafts cached as dry-run)."
    )
    if result["engine"] == "template" and cfg.resolved_provider is None:
        key = "GROQ_API_KEY" if cfg.llm.provider in ("groq", "auto") else "ANTHROPIC_API_KEY"
        console.print(
            f"[yellow]No {key}[/yellow] — used the offline template drafter (no billing). "
            f"Set the key (and `uv sync --extra groq` for Groq) to draft with the LLM."
        )


@app.command()
def send(
    do_send: bool = typer.Option(False, "--send", help="Arm the real send/submit path (still requires --i-confirm)."),
    i_confirm: bool = typer.Option(False, "--i-confirm", help="Second confirmation required alongside --send."),
    demo_fixtures: bool = typer.Option(False, "--demo-fixtures", help="ATS adapters navigate bundled local form fixtures (safe offline demo)."),
    limit: int = typer.Option(None, "--limit", "-n", help="Process only the top N drafts by fit score."),
) -> None:
    """Prepare applications to ./outbox/ per channel. Sends/submits NOTHING by default.

    Per drafted job, routed by channel: email -> ready-to-send .eml;
    Greenhouse/Lever -> field-map .json + review screenshot (Playwright).
    Real send/submit is gated: pass BOTH --send AND --i-confirm, and even then a
    message/application only leaves the machine when its own validation passes
    (email: a recipient; ATS: zero needs-review fields) and credentials/browsers
    exist — otherwise it falls back to the dry-run artifact.
    """
    import os

    from .submit import run_send

    if demo_fixtures:
        os.environ["JOBHUNTER_ATS_FIXTURES"] = "1"
    cfg = load_config()
    if do_send and not i_confirm:
        console.print("[red]Refusing to send:[/red] --send requires --i-confirm. Dry-running instead.")
        do_send = False
    result = run_send(cfg, do_send=do_send, confirm=i_confirm, limit=limit)
    c = result["counts"]
    parts = ", ".join(f"{k}={v}" for k, v in sorted(c.items()))
    console.print(
        f"[green]Send ({'LIVE' if result['live'] else 'dry-run'})[/green] to {result['outbox']} — {parts or 'nothing to do'}"
    )
    if not result["live"]:
        console.print("[dim]Nothing was transmitted/submitted. Real send needs `--send --i-confirm` + credentials/browser.[/dim]")


@app.command()
def run(dry_run: bool = typer.Option(True, "--dry-run/--live", help="Dry-run writes to ./outbox, never sends.")) -> None:
    """One full cycle: ingest -> parse -> filter -> score -> draft -> (dry-run outbox)."""
    _pending("run")


@app.command()
def watch(once: bool = typer.Option(False, "--once", help="Run a single cycle and exit (no loop).")) -> None:
    """Scheduler loop: run the full pipeline on an interval, DRY-RUN only (submits nothing)."""
    from .watch import run_cycle
    from .watch import watch as run_watch

    cfg = load_config()
    if once:
        summary = run_cycle(cfg)
        console.print(f"[green]Cycle done[/green] (dry-run, 0 submitted): {summary}")
        return
    console.print(
        f"[green]watch[/green] — running the pipeline every "
        f"{cfg.ingest.poll_interval_minutes}m (dry-run only). Ctrl-C to stop."
    )
    run_watch(cfg)


@app.command()
def apply(job_id: int = typer.Argument(..., help="Job id to prepare an application for.")) -> None:
    """Force a single application (still approval-gated, still dry-run by default)."""
    _pending("apply")


@app.command()
def jobs(status: Optional[str] = typer.Option(None, help="Filter by job status.")) -> None:
    """List jobs, optionally filtered by status (e.g. needs_human)."""
    cfg = load_config()
    store.init_db(cfg)
    with store.get_session(cfg) as session:
        from sqlalchemy import select

        q = select(store.Job).order_by(store.Job.fit_score.desc().nullslast())
        if status:
            try:
                q = q.where(store.Job.status == store.JobStatus(status))
            except ValueError:
                console.print(f"[red]Unknown status[/red] {status!r}. "
                              f"Valid: {[s.value for s in store.JobStatus]}")
                raise typer.Exit(1)
        rows = session.execute(q).scalars().all()

    if not rows:
        console.print("No jobs yet. Add posts with `jobhunter paste`, then `jobhunter run`.")
        return
    table = Table(show_header=True, header_style="bold")
    for col in ("id", "score", "status", "tier", "company", "title"):
        table.add_column(col)
    for j in rows:
        table.add_row(
            str(j.id),
            "" if j.fit_score is None else str(j.fit_score),
            j.status.value,
            "" if j.role_tier is None else str(j.role_tier),
            j.company or "",
            j.title or "",
        )
    console.print(table)


@app.command()
def stats() -> None:
    """Show counts by job status."""
    cfg = load_config()
    store.init_db(cfg)
    with store.get_session(cfg) as session:
        counts = store.count_by_status(session)
        total_raw = session.query(store.RawPost).count()
    console.print(f"[bold]raw posts:[/bold] {total_raw}")
    if not counts:
        console.print("No jobs parsed yet.")
        return
    table = Table(show_header=True, header_style="bold")
    table.add_column("status")
    table.add_column("count", justify="right")
    for status, n in sorted(counts.items()):
        table.add_row(status, str(n))
    console.print(table)


@app.command()
def undo(
    last: bool = typer.Option(False, "--last", help="Revert the most recent send record(s)."),
    count: int = typer.Option(1, "--count", "-n", help="How many recent records to revert."),
) -> None:
    """Reverse the most recent send record(s): remove ./outbox artifacts, un-mark the Application."""
    from .undo import run_undo

    if not last:
        console.print("Use: [bold]jobhunter undo --last[/bold] [--count N]")
        raise typer.Exit(1)
    cfg = load_config()
    report = run_undo(cfg, n=count)
    if report["count"] == 0:
        console.print("Nothing to undo — no send records with artifacts.")
        return
    for r in report["reverted"]:
        console.print(
            f"[green]Reverted[/green] app {r['application_id']} — "
            f"{r['title']} @ {r['company']} ({r['channel']}); "
            f"removed {len(r['removed_files'])} file(s); job back to 'scored'."
        )
        if r["was_real_send"]:
            console.print(
                f"[red]  ! this was a REAL send[/red] (ref {r['external_ref']}) — the "
                f"recipient already has it; only the local record was reverted."
            )
    if not report["real_send_warning"]:
        console.print("[dim]All reverted records were dry-run only — nothing had been sent.[/dim]")


@app.command()
def pause() -> None:
    """Engage the kill switch (writes the PAUSED file). Halts all submission."""
    PAUSED_FILE.write_text("paused\n", encoding="utf-8")
    console.print("[red]PAUSED[/red] — submission halted. `jobhunter resume` to clear.")


@app.command()
def resume() -> None:
    """Clear the kill switch (removes the PAUSED file)."""
    if PAUSED_FILE.exists():
        PAUSED_FILE.unlink()
    console.print("[green]Resumed[/green] — kill switch cleared.")


if __name__ == "__main__":
    app()
