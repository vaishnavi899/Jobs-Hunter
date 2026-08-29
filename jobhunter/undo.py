"""Undo the most recent send record(s).

For a dry-run artifact (.eml / ATS field-map + screenshot), `undo` removes the
outbox files and un-marks the Application so the job can be reworked.

HONEST LIMITATION: a truly-sent email or a truly-submitted ATS application
cannot be un-sent — the recipient/ATS already has it. `undo` only reverts the
LOCAL record and deletes local artifacts; it prints a clear warning for any
record that was a real send so you can follow up manually.
"""

from __future__ import annotations

from pathlib import Path

from .config import Config, load_config
from .store import Application, ApplicationStatus, Job, JobStatus, get_session, init_db


def _artifact_paths(app: Application) -> list[Path]:
    paths: list[Path] = []
    for p in (app.outbox_path, app.screenshot_path):
        if p:
            paths.append(Path(p))
    # ATS confirmation screenshot, if a real submit ever produced one.
    if app.screenshot_path and app.screenshot_path.endswith(".png"):
        confirm = Path(app.screenshot_path[:-4] + ".confirm.png")
        paths.append(confirm)
    return paths


def run_undo(cfg: Config | None = None, n: int = 1) -> dict:
    """Revert the most recent `n` send records. Returns a report."""
    cfg = cfg or load_config()
    init_db(cfg)
    reverted: list[dict] = []
    with get_session(cfg) as session:
        apps = (
            session.query(Application)
            .filter(
                (Application.outbox_path.isnot(None))
                | (Application.screenshot_path.isnot(None))
            )
            .order_by(Application.created_at.desc())
            .limit(n)
            .all()
        )
        for app in apps:
            job = app.job
            was_real_send = (not app.dry_run) and app.status == ApplicationStatus.submitted
            removed = []
            for path in _artifact_paths(app):
                if path.exists():
                    try:
                        path.unlink()
                        removed.append(str(path))
                    except OSError:
                        pass
            reverted.append({
                "application_id": app.id,
                "job_id": job.id if job else None,
                "company": job.company if job else None,
                "title": job.title if job else None,
                "channel": app.channel.value,
                "was_real_send": was_real_send,
                "external_ref": app.external_ref,
                "removed_files": removed,
            })
            # Revert the local record; let the job be reworked.
            if job is not None:
                job.status = JobStatus.scored
                job.filter_reason = None
            session.delete(app)
        session.commit()

    return {
        "reverted": reverted,
        "count": len(reverted),
        "real_send_warning": any(r["was_real_send"] for r in reverted),
    }
