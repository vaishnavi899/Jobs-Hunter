"""Filter — hard criteria from config.yaml.

Rejects on CONFIRMED disqualifiers only:
  - confirmed full-time comp below the annual floor (8 LPA)
  - confirmed internship stipend below the monthly floor (35k/mo)
  - posting demands >= 2 years experience
  - fee required / unpaid internship
  - onsite outside India when allow_international is false

Unknown salary is NEVER rejected here — it is flagged (salary_unknown) upstream
and ranked lower at scoring time. A job with no confirmed below-band number
survives. Salary is already normalized to annual INR by parse.py.
"""

from __future__ import annotations

import re

from .config import Config, load_config
from .parse import FOREIGN_LOCATIONS, INDIA_LOCATIONS
from .store import EmploymentType, Job, JobStatus, get_session, init_db


def _fee_or_unpaid(job: Job, cfg: Config) -> str | None:
    text = f"{job.title or ''} {job.company or ''}".lower()
    raw = (job.raw_post.content if job.raw_post else "") or ""
    blob = f"{text} {raw}".lower()
    if cfg.filters.reject_if_fee_required and re.search(
        r"\b(registration fee|pay .* fee|processing fee|deposit required|"
        r"security deposit|pay to apply)\b", blob
    ):
        return "fee required"
    if (
        cfg.filters.reject_if_unpaid_internship
        and job.employment_type == EmploymentType.internship
        and re.search(r"\bunpaid\b", blob)
    ):
        return "unpaid internship"
    return None


def is_india_or_remote(job: Job) -> bool:
    """True unless the job is CONFIRMED onsite outside India.

    Remote (incl. remote-India) always qualifies. Unknown location qualifies —
    many Indian startup posts never name a city, so we don't drop on unknown.
    """
    if job.remote:
        return True
    loc = (job.location or "").strip().lower()
    if not loc:
        return True  # unknown location -> don't drop
    if any(city in loc for city in FOREIGN_LOCATIONS):
        return False
    if any(re.search(rf"\b{re.escape(city)}\b", loc) for city in INDIA_LOCATIONS):
        return True
    if "india" in loc:
        return True
    # A named, non-India, non-remote location -> treat as international.
    return False


def salary_floor_annual_inr(job: Job, cfg: Config) -> int:
    if job.employment_type == EmploymentType.internship:
        return cfg.filters.internship_min_inr_per_month * cfg.salary.months_per_year
    return cfg.filters.fulltime_min_inr_per_year


def passes_filter(job: Job, cfg: Config) -> tuple[bool, str | None]:
    """Return (survives, reason_if_rejected)."""
    # Experience: reject if the posting DEMANDS more than the max we allow.
    if (
        job.experience_required_years is not None
        and job.experience_required_years > cfg.filters.max_experience_years
    ):
        return False, (
            f"requires {job.experience_required_years:g} yrs "
            f"(> {cfg.filters.max_experience_years})"
        )

    # Fee / unpaid internship.
    reason = _fee_or_unpaid(job, cfg)
    if reason:
        return False, reason

    # International onsite.
    if not cfg.filters.allow_international and not is_india_or_remote(job):
        return False, f"onsite outside India ({job.location})"

    # Salary: reject only on a CONFIRMED below-floor number.
    if not job.salary_unknown:
        floor = salary_floor_annual_inr(job, cfg)
        top = job.salary_max_inr if job.salary_max_inr is not None else job.salary_min_inr
        if top is not None and top < floor:
            unit = "LPA" if job.employment_type != EmploymentType.internship else "INR/yr-equiv"
            return False, f"confirmed below band ({top/100000:g} {unit} < floor)"
    elif cfg.filters.reject_on_unknown_salary:
        return False, "salary unknown (reject_on_unknown_salary=true)"

    return True, None


def run_filter(cfg: Config | None = None) -> dict:
    """Evaluate every `new` job. Survivors -> passed_filter; rest -> filtered_out."""
    cfg = cfg or load_config()
    init_db(cfg)
    passed = rejected = 0
    with get_session(cfg) as session:
        jobs = session.query(Job).filter(Job.status == JobStatus.new).all()
        for job in jobs:
            ok, reason = passes_filter(job, cfg)
            if ok:
                job.status = JobStatus.passed_filter
                job.filter_reason = None
                passed += 1
            else:
                job.status = JobStatus.filtered_out
                job.filter_reason = reason
                rejected += 1
        session.commit()
    return {"passed": passed, "rejected": rejected}
