"""Persistence — SQLAlchemy 2.0 models + session/query helpers (SQLite).

Tables:
  raw_posts       every ingested post, original payload never discarded
  jobs            parsed + filtered + scored structured job
  applications    every send attempt (dry-run or live), full audit trail
  parse_failures  quarantine for posts that would not parse to valid JSON

The schema is the Checkpoint-1 deliverable; the pipeline modules that fill it
arrive in later checkpoints.
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    func,
    select,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)

from .config import Config, load_config


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


# --------------------------------------------------------------------------- #
# Enums                                                                         #
# --------------------------------------------------------------------------- #
class JobStatus(str, enum.Enum):
    new = "new"                # freshly parsed
    filtered_out = "filtered_out"
    scored = "scored"          # passed filter, has a fit score
    drafted = "drafted"        # application drafted to ./outbox
    awaiting_approval = "awaiting_approval"
    approved = "approved"
    rejected = "rejected"      # human declined
    submitted = "submitted"
    needs_human = "needs_human"
    skipped = "skipped"


class EmploymentType(str, enum.Enum):
    fulltime = "fulltime"
    internship = "internship"
    contract = "contract"
    unknown = "unknown"


class ApplyMethod(str, enum.Enum):
    ats_greenhouse = "ats_greenhouse"
    ats_lever = "ats_lever"
    ats_ashby = "ats_ashby"
    ats_workable = "ats_workable"
    ats_workday = "ats_workday"
    email = "email"
    form = "form"
    dm = "dm"
    unknown = "unknown"


class ApplicationStatus(str, enum.Enum):
    dry_run = "dry_run"        # written to outbox, nothing sent
    pending_approval = "pending_approval"
    approved = "approved"
    submitted = "submitted"
    failed = "failed"
    needs_human = "needs_human"
    undone = "undone"


# --------------------------------------------------------------------------- #
# Models                                                                        #
# --------------------------------------------------------------------------- #
class RawPost(Base):
    """A single ingested post, exactly as received. Never mutated after insert."""

    __tablename__ = "raw_posts"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(32))          # e.g. "manual", "x_api"
    external_id: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    posted_by_handle: Mapped[Optional[str]] = mapped_column(String(128))
    source_url: Mapped[Optional[str]] = mapped_column(String(1024))
    content: Mapped[str] = mapped_column(Text)               # raw text of the post
    raw_payload: Mapped[Optional[dict]] = mapped_column(JSON)  # full original object
    content_hash: Mapped[str] = mapped_column(String(64), index=True)  # dedupe key
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    job: Mapped[Optional["Job"]] = relationship(back_populates="raw_post", uselist=False)

    __table_args__ = (UniqueConstraint("content_hash", name="uq_rawpost_hash"),)


class Job(Base):
    """Structured job parsed from a RawPost, plus filter/score outcome."""

    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    raw_post_id: Mapped[int] = mapped_column(ForeignKey("raw_posts.id"), unique=True)

    company: Mapped[Optional[str]] = mapped_column(String(256), index=True)
    title: Mapped[Optional[str]] = mapped_column(String(256))
    role_tier: Mapped[Optional[int]] = mapped_column(Integer)
    employment_type: Mapped[EmploymentType] = mapped_column(
        Enum(EmploymentType), default=EmploymentType.unknown
    )
    salary_min_inr: Mapped[Optional[float]] = mapped_column(Float)
    salary_max_inr: Mapped[Optional[float]] = mapped_column(Float)
    salary_unknown: Mapped[bool] = mapped_column(Boolean, default=True)
    experience_required_years: Mapped[Optional[float]] = mapped_column(Float)
    location: Mapped[Optional[str]] = mapped_column(String(256))
    remote: Mapped[Optional[bool]] = mapped_column(Boolean)
    apply_method: Mapped[ApplyMethod] = mapped_column(Enum(ApplyMethod), default=ApplyMethod.unknown)
    apply_target: Mapped[Optional[str]] = mapped_column(String(1024))
    tech_stack: Mapped[Optional[list]] = mapped_column(JSON)
    confidence: Mapped[Optional[float]] = mapped_column(Float)

    fit_score: Mapped[Optional[int]] = mapped_column(Integer, index=True)
    score_breakdown: Mapped[Optional[dict]] = mapped_column(JSON)
    filter_reason: Mapped[Optional[str]] = mapped_column(String(512))  # why filtered_out

    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.new, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    raw_post: Mapped["RawPost"] = relationship(back_populates="job")
    applications: Mapped[list["Application"]] = relationship(back_populates="job")


class Application(Base):
    """Every send attempt — dry-run or live. Full audit trail per acceptance criteria."""

    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)

    channel: Mapped[ApplyMethod] = mapped_column(Enum(ApplyMethod))
    status: Mapped[ApplicationStatus] = mapped_column(
        Enum(ApplicationStatus), default=ApplicationStatus.dry_run, index=True
    )

    subject: Mapped[Optional[str]] = mapped_column(String(512))
    body: Mapped[Optional[str]] = mapped_column(Text)          # full sent content
    resume_bullets: Mapped[Optional[list]] = mapped_column(JSON)  # reordered top-6
    outbox_path: Mapped[Optional[str]] = mapped_column(String(1024))
    screenshot_path: Mapped[Optional[str]] = mapped_column(String(1024))
    external_ref: Mapped[Optional[str]] = mapped_column(String(512))  # gmail thread id, etc.
    error: Mapped[Optional[str]] = mapped_column(Text)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    job: Mapped["Job"] = relationship(back_populates="applications")


class ParseFailure(Base):
    """Quarantine for posts that would not parse — never crash the pipeline."""

    __tablename__ = "parse_failures"

    id: Mapped[int] = mapped_column(primary_key=True)
    raw_post_id: Mapped[Optional[int]] = mapped_column(ForeignKey("raw_posts.id"))
    error: Mapped[str] = mapped_column(Text)
    raw_llm_output: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


# --------------------------------------------------------------------------- #
# Engine / session helpers                                                      #
# --------------------------------------------------------------------------- #
_engine = None
_SessionFactory: Optional[sessionmaker[Session]] = None


def get_engine(cfg: Config | None = None):
    global _engine, _SessionFactory
    if _engine is None:
        cfg = cfg or load_config()
        _engine = create_engine(cfg.db_url, future=True)
        _SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def get_session(cfg: Config | None = None) -> Session:
    get_engine(cfg)
    assert _SessionFactory is not None
    return _SessionFactory()


def init_db(cfg: Config | None = None) -> None:
    """Create all tables if they do not exist. Idempotent."""
    engine = get_engine(cfg)
    Base.metadata.create_all(engine)


# --------------------------------------------------------------------------- #
# Small query helpers used by the CLI                                           #
# --------------------------------------------------------------------------- #
def count_by_status(session: Session) -> dict[str, int]:
    rows = session.execute(select(Job.status, func.count()).group_by(Job.status)).all()
    return {status.value: n for status, n in rows}


def applications_today(session: Session, channel: ApplyMethod) -> int:
    """Count non-dry-run sends of `channel` created today (for daily caps)."""
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return (
        session.execute(
            select(func.count())
            .select_from(Application)
            .where(
                Application.channel == channel,
                Application.dry_run.is_(False),
                Application.created_at >= start,
            )
        ).scalar_one()
    )
