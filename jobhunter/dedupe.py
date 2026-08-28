"""Dedupe — content-hash (ingest) + per-company 30-day rule (apply).

Two layers:
  1. Ingest dedupe: skip any RawPost whose content_hash was already stored.
     This is what makes re-ingesting the same post a no-op.
  2. Apply dedupe: never apply to the same company twice within 30 days
     (used at submission time, Checkpoint 5+).
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .store import Application, ApplicationStatus, Job, RawPost


def content_hash(text: str) -> str:
    """Stable dedupe key: lowercased, whitespace-collapsed sha256 of the post."""
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def already_seen(session: Session, hash_: str) -> bool:
    """True if a RawPost with this content_hash already exists."""
    return (
        session.execute(
            select(func.count()).select_from(RawPost).where(RawPost.content_hash == hash_)
        ).scalar_one()
        > 0
    )


def company_applied_recently(session: Session, company: str, within_days: int = 30) -> bool:
    """True if a real (non-dry-run) application to `company` exists within the window.

    Used to enforce the per-company 30-day apply rule at submission time.
    """
    if not company:
        return False
    cutoff = datetime.now(timezone.utc) - timedelta(days=within_days)
    count = session.execute(
        select(func.count())
        .select_from(Application)
        .join(Job, Application.job_id == Job.id)
        .where(
            func.lower(Job.company) == company.strip().lower(),
            Application.dry_run.is_(False),
            Application.status.in_(
                [ApplicationStatus.submitted, ApplicationStatus.approved]
            ),
            Application.created_at >= cutoff,
        )
    ).scalar_one()
    return count > 0
