"""Safety rails for the send/submit path — all default to the safe state.

A real send/submit is allowed only when EVERY rail passes:
  - not paused (the PAUSED kill switch)
  - under the per-channel daily cap AND the global daily cap
  - under the per-company/day cap
  - not a per-company 30-day duplicate

Any one failing -> no send, with a clear reason. These are consulted only on
the live send path; dry-run always writes ./outbox/ artifacts (so `watch` keeps
discovering/drafting even while paused). Company dedupe is also enforced at
draft time so duplicates never get drafted or notified in the first place.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import Config
from .store import Application, ApplyMethod, Job

_SUFFIXES = (
    "private limited", "pvt ltd", "pvt. ltd.", "technologies", "technology",
    "labs", "systems", "solutions", "software", "inc", "llc", "ltd", "co",
    "corp", "gmbh", "ai",
)


def normalize_company(name: str | None) -> str:
    if not name:
        return ""
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    for suf in sorted(_SUFFIXES, key=len, reverse=True):
        if s.endswith(" " + suf):
            s = s[: -len(suf)].strip()
    return s


def _start_of_today() -> datetime:
    return datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


def company_engaged_recently(
    session: Session, company: str | None, days: int, exclude_job_id: int | None = None
) -> Job | None:
    """Return an existing Job of the same (normalized) company that already has
    an Application within the window, or None. Powers the 30-day dedupe.
    """
    key = normalize_company(company)
    if not key:
        return None
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (
        session.query(Application, Job)
        .join(Job, Application.job_id == Job.id)
        .filter(Application.created_at >= cutoff)
        .all()
    )
    for _app, job in rows:
        if exclude_job_id is not None and job.id == exclude_job_id:
            continue
        if normalize_company(job.company) == key:
            return job
    return None


def _sent_today(session: Session, channel: ApplyMethod | None = None) -> int:
    start = _start_of_today()
    q = (
        select(func.count())
        .select_from(Application)
        .where(Application.dry_run.is_(False), Application.sent_at >= start)
    )
    if channel is not None:
        q = q.where(Application.channel == channel)
    return session.execute(q).scalar_one()


def _sent_today_for_company(session: Session, company: str | None) -> int:
    start = _start_of_today()
    key = normalize_company(company)
    if not key:
        return 0
    rows = (
        session.query(Application, Job)
        .join(Job, Application.job_id == Job.id)
        .filter(Application.dry_run.is_(False), Application.sent_at >= start)
        .all()
    )
    return sum(1 for _a, j in rows if normalize_company(j.company) == key)


_ATS_CHANNELS = {
    ApplyMethod.ats_greenhouse, ApplyMethod.ats_lever,
    ApplyMethod.ats_ashby, ApplyMethod.ats_workable, ApplyMethod.ats_workday,
}


def send_allowed(session: Session, job: Job, cfg: Config) -> tuple[bool, str | None]:
    """Gate the live send path. Returns (allowed, reason_if_blocked)."""
    if cfg.is_paused:
        return False, "paused (kill switch active) — /resume or `jobhunter resume` to clear"

    # per-channel daily cap
    if job.apply_method == ApplyMethod.email:
        if _sent_today(session, ApplyMethod.email) >= cfg.caps.emails_per_day:
            return False, f"daily email cap reached ({cfg.caps.emails_per_day})"
    elif job.apply_method in _ATS_CHANNELS:
        ats_today = sum(_sent_today(session, m) for m in _ATS_CHANNELS)
        if ats_today >= cfg.caps.ats_submissions_per_day:
            return False, f"daily ATS cap reached ({cfg.caps.ats_submissions_per_day})"

    # global daily cap
    if _sent_today(session) >= cfg.caps.applications_per_day:
        return False, f"daily applications cap reached ({cfg.caps.applications_per_day})"

    # per-company/day cap
    if _sent_today_for_company(session, job.company) >= cfg.caps.per_company_per_day:
        return False, f"per-company daily cap reached ({cfg.caps.per_company_per_day}) for {job.company}"

    # per-company 30-day dedupe (another job of the same company already engaged)
    dup = company_engaged_recently(
        session, job.company, cfg.submission.company_dedupe_days, exclude_job_id=job.id
    )
    if dup is not None:
        return False, (
            f"per-company {cfg.submission.company_dedupe_days}-day dedupe — already "
            f"engaged {job.company} via job {dup.id}"
        )
    return True, None
