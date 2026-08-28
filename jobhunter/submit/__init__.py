"""Submission adapters + router + dry-run orchestrator.

APPROVAL-GATED, dry-run by default. Nothing sends automatically. Every drafted
application is written to ./outbox/ as a ready-to-send .eml; a real send only
happens behind an explicit human gate (`--send --i-confirm`) and only when the
job has a real recipient and credentials exist.

Checkpoint 5 ships the email adapter. ATS/DM adapters drop into this package
later behind the same `Adapter` protocol (base.py) without touching the router
or the orchestrator.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from ..config import Config, load_config
from ..store import (
    Application,
    ApplicationStatus,
    Job,
    JobStatus,
    get_session,
    init_db,
)
from .base import Adapter, SubmitResult
from .email import OUTBOX, EmailAdapter

__all__ = ["get_adapter", "SubmitResult", "Adapter", "run_send"]


def get_adapter(method):
    """Return the adapter instance for an apply method.

    Only the email adapter is built (Checkpoint 5). The rest raise until their
    build-order checkpoint; the Checkpoint-5 dry-run writes a universal .eml
    artifact via the email adapter regardless of channel.
    """
    from ..store import ApplyMethod

    if method == ApplyMethod.email:
        return EmailAdapter()
    raise NotImplementedError(f"adapter for {method} is built in a later checkpoint")


def run_send(
    cfg: Config | None = None,
    do_send: bool = False,
    confirm: bool = False,
    limit: int | None = None,
) -> dict:
    """Dry-run every drafted job to ./outbox/ as a .eml. Send nothing by default.

    A real send is attempted ONLY when both `do_send` and `confirm` are true
    (the CLI's `--send --i-confirm`); even then it dry-runs unless the job has a
    real recipient and Gmail credentials exist. Idempotent: a job already
    written (or already submitted) is skipped, never double-written/double-sent.
    """
    cfg = cfg or load_config()
    init_db(cfg)
    adapter = EmailAdapter()
    live = bool(do_send and confirm)
    now = datetime.now(timezone.utc)

    counts = {
        "written": 0, "skipped_existing": 0, "skipped_sent": 0,
        "sent": 0, "needs_auth": 0, "needs_human": 0, "failed": 0,
    }
    sample_path = None

    with get_session(cfg) as session:
        apps = (
            session.query(Application)
            .join(Job, Application.job_id == Job.id)
            .order_by(Job.fit_score.desc().nullslast())
            .all()
        )
        if limit is not None:
            apps = apps[:limit]

        for app in apps:
            job = app.job
            if app.status == ApplicationStatus.submitted:
                counts["skipped_sent"] += 1
                continue

            if not live:
                if app.outbox_path and Path(app.outbox_path).exists():
                    counts["skipped_existing"] += 1
                    sample_path = sample_path or app.outbox_path
                    continue
                res = adapter.dry_run(job, app, cfg)
                app.outbox_path = res.outbox_path
                app.status = ApplicationStatus.dry_run
                app.dry_run = True
                counts["written"] += 1
                sample_path = sample_path or res.outbox_path
                continue

            # live (gated) path — still safe: send() falls back to a .eml when
            # there's no recipient or no credentials, and never raises.
            res = adapter.send(job, app, cfg)
            if res.action == "sent":
                app.status = ApplicationStatus.submitted
                app.dry_run = False
                app.external_ref = res.external_ref
                app.sent_at = now
                counts["sent"] += 1
            else:
                app.outbox_path = res.outbox_path or app.outbox_path
                app.status = ApplicationStatus.dry_run
                app.dry_run = True
                key = {"needs_auth": "needs_auth", "needs_human": "needs_human"}.get(
                    res.action, "failed"
                )
                counts[key] += 1
                sample_path = sample_path or res.outbox_path
        session.commit()

    return {
        "counts": counts,
        "live": live,
        "outbox": str(OUTBOX),
        "sample": sample_path,
    }
