"""Scheduler loop — runs the full pipeline on an interval, DRY-RUN only.

Each cycle: poll Telegram commands -> ingest -> parse -> filter -> score ->
draft (with per-company dedupe) -> send-DRY-RUN (writes ./outbox/ artifacts) ->
notify for newly drafted jobs. It NEVER submits — it only prepares artifacts and
waits for explicit human approval. Survives an empty inbox and missing
Telegram/browser/LLM credentials; logs every cycle.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .config import PROJECT_ROOT, Config, load_config
from .draft import run_draft
from .filter import run_filter
from .ingest import run_ingest
from .notify.telegram import LAST_CYCLE_PATH, TelegramNotifier
from .parse import run_parse
from .score import run_score
from .store import Application, Job, JobStatus, get_session, init_db
from .submit import run_send

WATCH_LOG = PROJECT_ROOT / "logs" / "watch.log"


def _log(line: str) -> None:
    WATCH_LOG.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with WATCH_LOG.open("a", encoding="utf-8") as fh:
        fh.write(f"[{ts}] {line}\n")


def run_cycle(cfg: Config | None = None, notifier: TelegramNotifier | None = None) -> dict:
    """One full dry-run pipeline cycle. Submits nothing."""
    cfg = cfg or load_config()
    init_db(cfg)
    notifier = notifier or TelegramNotifier(cfg)

    notifier.poll_commands()  # honor any pending /pause /resume /status first

    with get_session(cfg) as s:
        before = {a for (a,) in s.query(Application.job_id).all()}

    ing = run_ingest(cfg)
    par = run_parse(cfg)
    fil = run_filter(cfg)
    sco = run_score(cfg)
    dra = run_draft(cfg)
    snd = run_send(cfg)  # do_send=False -> dry-run artifacts only

    # Notify for jobs newly drafted this cycle.
    notified = 0
    with get_session(cfg) as s:
        new_apps = (
            s.query(Application, Job)
            .join(Job, Application.job_id == Job.id)
            .filter(~Application.job_id.in_(before) if before else Application.job_id.isnot(None))
            .order_by(Job.fit_score.desc().nullslast())
            .all()
        )
        for app, job in new_apps:
            if job.id in before:
                continue
            state = "needs review" if job.status == JobStatus.needs_human else "ready (dry-run)"
            notifier.notify_new_job(job, state)
            notified += 1

    paused = cfg.is_paused
    summary = {
        "ingest": ing.added, "parsed": par["parsed"],
        "passed_filter": fil["passed"], "scored": sco["scored"],
        "drafted": dra["drafted"], "deduped": dra.get("deduped", 0),
        "prepared": snd["counts"], "notified": notified,
        "paused": paused, "submitted": 0,
    }
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    LAST_CYCLE_PATH.parent.mkdir(parents=True, exist_ok=True)
    LAST_CYCLE_PATH.write_text(ts, encoding="utf-8")
    _log(f"cycle: {summary}")
    return summary


def watch(cfg: Config | None = None, cycles: int | None = None) -> None:
    """Run cycles on the configured interval (blocking). `cycles=1` = one pass."""
    cfg = cfg or load_config()
    notifier = TelegramNotifier(cfg)
    run_cycle(cfg, notifier)  # immediate first cycle
    if cycles == 1:
        return
    interval = max(1, cfg.ingest.poll_interval_minutes)
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
    except ImportError:
        _log("apscheduler not installed; ran a single cycle. `uv sync` to enable the loop.")
        return
    sched = BlockingScheduler(timezone="UTC")
    sched.add_job(lambda: run_cycle(cfg, notifier), "interval", minutes=interval,
                  id="jobhunter_cycle")
    _log(f"scheduler started; interval={interval}m")
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        sched.shutdown(wait=False)
        _log("scheduler stopped")
