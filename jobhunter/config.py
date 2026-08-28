"""Configuration loading — reads config.yaml and .env, validates with pydantic.

Everything tunable lives in config.yaml so it can be edited without touching
Python. Secrets come from .env (never committed).
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"
PAUSED_FILE = PROJECT_ROOT / "PAUSED"


class RolesConfig(BaseModel):
    tier1: list[str] = Field(default_factory=list)
    tier2: list[str] = Field(default_factory=list)
    tier3: list[str] = Field(default_factory=list)


class FiltersConfig(BaseModel):
    fulltime_min_inr_per_year: int = 800_000
    internship_min_inr_per_month: int = 35_000
    max_experience_years: int = 1
    reject_if_fee_required: bool = True
    reject_if_unpaid_internship: bool = True
    reject_on_unknown_salary: bool = False
    allow_international: bool = False


class SalaryConfig(BaseModel):
    usd_to_inr: float = 84.0
    months_per_year: int = 12


class LLMConfig(BaseModel):
    parse_model: str = "claude-haiku-4-5"
    draft_model: str = "claude-sonnet-5"
    engine: str = "auto"  # auto | llm | heuristic
    fetch_linked_pages: bool = True
    request_timeout_seconds: int = 30
    max_page_chars: int = 12000


class ScoringConfig(BaseModel):
    role_tier: dict[str, int] = Field(default_factory=lambda: {"tier1": 40, "tier2": 25, "tier3": 10})
    location_india_or_remote: int = 10
    salary_confirmed_in_band: int = 25
    salary_unknown: int = 12
    tech_stack_overlap_max: int = 20
    recency_max: int = 5


class ManualIngestConfig(BaseModel):
    inbox_dir: str = "./inbox"
    archive_processed: bool = True


class IngestConfig(BaseModel):
    active_source: str = "manual"
    manual: ManualIngestConfig = Field(default_factory=ManualIngestConfig)
    poll_interval_minutes: int = 20


class SubmissionConfig(BaseModel):
    dry_run_default: bool = True
    require_approval: bool = True
    auto_send_channels: list[str] = Field(default_factory=list)
    delay_seconds_min: int = 30
    delay_seconds_max: int = 90


class CapsConfig(BaseModel):
    emails_per_day: int = 25
    ats_submissions_per_day: int = 15


class SignatureConfig(BaseModel):
    name: str = "Vaishnavi"
    availability: str = "Available in 30 days (currently serving a 30-day notice period)."


class Config(BaseModel):
    roles: RolesConfig = Field(default_factory=RolesConfig)
    filters: FiltersConfig = Field(default_factory=FiltersConfig)
    salary: SalaryConfig = Field(default_factory=SalaryConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    ingest: IngestConfig = Field(default_factory=IngestConfig)
    submission: SubmissionConfig = Field(default_factory=SubmissionConfig)
    caps: CapsConfig = Field(default_factory=CapsConfig)
    signature: SignatureConfig = Field(default_factory=SignatureConfig)

    # --- convenience for env-derived runtime values (not from config.yaml) ---
    @property
    def db_url(self) -> str:
        db_path = os.getenv("JOBHUNTER_DB", str(PROJECT_ROOT / "jobhunter.db"))
        return f"sqlite:///{db_path}"

    @property
    def is_paused(self) -> bool:
        return PAUSED_FILE.exists()

    @property
    def anthropic_api_key(self) -> str | None:
        return os.getenv("ANTHROPIC_API_KEY") or None


RESUME_PATH = PROJECT_ROOT / "profile" / "resume.json"


def load_resume() -> dict | None:
    """Load profile/resume.json (the structured drafting source), or None.

    profile/ is gitignored — this is Vaishnavi's personal data, read locally.
    """
    if RESUME_PATH.exists():
        return json.loads(RESUME_PATH.read_text(encoding="utf-8"))
    return None


@lru_cache(maxsize=1)
def load_config(path: str | os.PathLike[str] | None = None) -> Config:
    """Load .env then config.yaml into a validated Config. Cached per process."""
    load_dotenv(PROJECT_ROOT / ".env")
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    data: dict = {}
    if cfg_path.exists():
        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    return Config(**data)
