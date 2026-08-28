"""Score — 0-100 fit ranking, weights from config.yaml.

Location is flat (India-or-remote = a fixed number of points, no city
preference), so tech-stack overlap against resume.json is the main tiebreaker.
That overlap is computed as a proper skill-coverage fraction over a canonical
skill vocabulary (synonyms folded together), not a raw keyword count.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from .config import Config, load_config, load_resume
from .filter import is_india_or_remote
from .store import Job, JobStatus, get_session, init_db

# Fold common surface variants onto one canonical skill token so, e.g.,
# "Node.js" / "nodejs" / "node" all count as the same skill, and "RAG
# pipelines" matches "RAG". This is what makes the overlap semantic rather
# than a literal string count.
_SYNONYMS = {
    "nodejs": "node", "node.js": "node",
    "reactjs": "react", "react.js": "react",
    "nextjs": "next", "next.js": "next",
    "expressjs": "express", "express.js": "express",
    "js": "javascript",
    "ts": "typescript",
    "postgresql": "postgres",
    "restful apis": "rest", "rest apis": "rest", "restful": "rest",
    "rag pipelines": "rag", "rag pipeline": "rag",
    "machine learning": "ml",
    "llm fine-tuning": "fine-tuning", "fine tuning": "fine-tuning",
    "genai": "llm", "llm engineer": "llm", "large language models": "llm",
    "prompt engineering": "prompt-engineering",
    "gcp": "gcp", "google cloud": "gcp",
    "vector db": "vector", "vector database": "vector", "embeddings": "vector",
}


def _canon(token: str) -> str:
    t = re.sub(r"\s+", " ", token.strip().lower())
    return _SYNONYMS.get(t, t)


def canonical_skills(items) -> set[str]:
    return {_canon(x) for x in (items or []) if str(x).strip()}


def tech_overlap_points(job_stack, resume_skills, max_pts: int) -> tuple[int, float]:
    """Points = max_pts * (fraction of the job's required skills she has).

    Coverage of the JOB's ask (how much of what they want does she bring),
    which rewards a tight match and is stable regardless of resume length.
    Returns (points, coverage).
    """
    js = canonical_skills(job_stack)
    if not js:
        return 0, 0.0
    rs = canonical_skills(resume_skills)
    coverage = len(js & rs) / len(js)
    return round(max_pts * coverage), coverage


def recency_points(ingested_at: datetime | None, max_pts: int, now: datetime) -> int:
    """Full points under 24h, linear decay to 0 at 7 days."""
    if ingested_at is None:
        return 0
    if ingested_at.tzinfo is None:
        ingested_at = ingested_at.replace(tzinfo=timezone.utc)
    age_h = (now - ingested_at).total_seconds() / 3600
    if age_h <= 24:
        return max_pts
    if age_h >= 168:
        return 0
    return round(max_pts * (168 - age_h) / (168 - 24))


def score_job(job: Job, cfg: Config, resume: dict | None, now: datetime | None = None) -> tuple[int, dict]:
    now = now or datetime.now(timezone.utc)
    w = cfg.scoring
    resume_skills = (resume or {}).get("skills_flat", [])

    tier_pts = w.role_tier.get(f"tier{job.role_tier}", 0) if job.role_tier else 0
    loc_pts = w.location_india_or_remote if is_india_or_remote(job) else 0
    sal_pts = w.salary_unknown if job.salary_unknown else w.salary_confirmed_in_band
    tech_pts, coverage = tech_overlap_points(job.tech_stack, resume_skills, w.tech_stack_overlap_max)
    rec_pts = recency_points(job.raw_post.ingested_at if job.raw_post else None, w.recency_max, now)

    total = tier_pts + loc_pts + sal_pts + tech_pts + rec_pts
    breakdown = {
        "role_tier": tier_pts,
        "location": loc_pts,
        "salary": sal_pts,
        "tech_overlap": tech_pts,
        "tech_coverage": round(coverage, 2),
        "recency": rec_pts,
        "total": total,
    }
    return total, breakdown


def run_score(cfg: Config | None = None) -> dict:
    """Score every passed_filter job -> scored, with fit_score + breakdown."""
    cfg = cfg or load_config()
    init_db(cfg)
    resume = load_resume()
    now = datetime.now(timezone.utc)
    scored = 0
    with get_session(cfg) as session:
        jobs = session.query(Job).filter(Job.status == JobStatus.passed_filter).all()
        for job in jobs:
            total, breakdown = score_job(job, cfg, resume, now)
            job.fit_score = total
            job.score_breakdown = breakdown
            job.status = JobStatus.scored
            scored += 1
        session.commit()
    return {"scored": scored, "resume_loaded": resume is not None}
