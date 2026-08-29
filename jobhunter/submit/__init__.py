"""Submission adapters + router + dry-run orchestrator.

APPROVAL-GATED, dry-run by default. Nothing sends/submits automatically.

Routing by the job's apply method:
  email          -> EmailAdapter        (.eml to ./outbox/)
  ats_greenhouse -> GreenhouseAdapter    (field-map .json + screenshot to ./outbox/)
  ats_lever      -> LeverAdapter         (field-map .json + screenshot to ./outbox/)
  anything else  -> needs_review         (adapter not built yet — clear reason)

A real send/submit only happens behind an explicit human gate (`--send
--i-confirm`) AND only when the artifact validates (email: a recipient exists;
ATS: zero needs_review fields) AND credentials/browsers exist. Idempotent via
the Application cache — a job already written/submitted is not redone.

Checkpoints: 5 shipped email; 6 ships Greenhouse + Lever. Ashby/Workable/
generic-form/DM drop into this package later behind the same Adapter protocol.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from ..config import Config, load_config
from ..store import (
    Application,
    ApplicationStatus,
    ApplyMethod,
    Job,
    get_session,
    init_db,
)
from .base import Adapter, SubmitResult
from .email import OUTBOX, EmailAdapter
from .greenhouse import GreenhouseAdapter
from .lever import LeverAdapter

__all__ = ["get_adapter", "SubmitResult", "Adapter", "run_send"]

_ATS_METHODS = {ApplyMethod.ats_greenhouse, ApplyMethod.ats_lever}


def get_adapter(method):
    """Return the adapter instance for an apply method, or None if not built."""
    if method == ApplyMethod.email:
        return EmailAdapter()
    if method == ApplyMethod.ats_greenhouse:
        return GreenhouseAdapter()
    if method == ApplyMethod.ats_lever:
        return LeverAdapter()
    return None


def _has_artifact(app: Application, method) -> bool:
    """True if a completed dry-run artifact already exists (idempotency)."""
    if method in _ATS_METHODS:
        return bool(app.screenshot_path and Path(app.screenshot_path).exists())
    return bool(app.outbox_path and Path(app.outbox_path).exists())


def _record(app: Application, res: SubmitResult, now: datetime) -> None:
    if res.outbox_path:
        app.outbox_path = res.outbox_path
    if res.screenshot:
        app.screenshot_path = res.screenshot
    if res.action == "submitted" or res.action == "sent":
        app.status = ApplicationStatus.submitted
        app.dry_run = False
        app.external_ref = res.external_ref
        app.sent_at = now
    elif res.action == "dry_run":
        app.status = ApplicationStatus.dry_run
        app.dry_run = True
    else:  # needs_review | needs_human | needs_auth | blocked_no_browser | failed
        app.status = ApplicationStatus.needs_human
        app.dry_run = True
        if res.note:
            app.error = res.note


def run_send(
    cfg: Config | None = None,
    do_send: bool = False,
    confirm: bool = False,
    limit: int | None = None,
) -> dict:
    """Dry-run every drafted job to ./outbox/. Send/submit nothing by default.

    A real send/submit is attempted ONLY when both `do_send` and `confirm` are
    true (the CLI's `--send --i-confirm`); even then each adapter only transmits
    when its own validation passes and credentials/browsers exist. Idempotent.
    """
    cfg = cfg or load_config()
    init_db(cfg)
    live = bool(do_send and confirm)
    now = datetime.now(timezone.utc)

    counts: dict[str, int] = {}

    def bump(key: str) -> None:
        counts[key] = counts.get(key, 0) + 1

    sample = {"eml": None, "ats_json": None, "ats_png": None}

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
            method = job.apply_method

            if app.status == ApplicationStatus.submitted:
                bump("skipped_sent")
                continue

            adapter = get_adapter(method)
            if adapter is None:
                app.status = ApplicationStatus.needs_human
                app.error = f"no adapter for apply_method={method.value} (built in a later checkpoint)"
                bump("needs_review")
                continue

            if not live and _has_artifact(app, method):
                bump("skipped_existing")
                continue

            res = adapter.send(job, app, cfg) if live else adapter.dry_run(job, app, cfg)
            _record(app, res, now)
            bump(res.action)

            if method == ApplyMethod.email and res.outbox_path:
                sample["eml"] = sample["eml"] or res.outbox_path
            if method in _ATS_METHODS:
                sample["ats_json"] = sample["ats_json"] or res.outbox_path
                sample["ats_png"] = sample["ats_png"] or res.screenshot
        session.commit()

    return {"counts": counts, "live": live, "outbox": str(OUTBOX), "sample": sample}
