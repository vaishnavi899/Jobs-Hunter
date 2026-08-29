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
    # provider: groq (default, free tier) | anthropic | auto
    #   groq      -> Groq's OpenAI-compatible API (GROQ_API_KEY)
    #   anthropic -> Anthropic API (ANTHROPIC_API_KEY), kept switchable
    #   auto      -> Groq if GROQ_API_KEY set, else Anthropic if its key set, else offline
    provider: str = "groq"
    # engine: auto -> use the resolved provider's LLM when its key is present,
    #                 else the offline heuristic parser / template drafter.
    engine: str = "auto"

    # Active-provider models. Defaults are current Groq production ids, verified
    # live against this account's /models endpoint: a small/fast model for bulk
    # parse and the strongest general model for drafting. To change, pick an id
    # your key exposes (list them: GET https://api.groq.com/openai/v1/models).
    # Common alternatives if your account has the Llama family instead:
    #   parse_model: llama-3.1-8b-instant   draft_model: llama-3.3-70b-versatile
    parse_model: str = "openai/gpt-oss-20b"
    draft_model: str = "openai/gpt-oss-120b"
    groq_base_url: str = "https://api.groq.com/openai/v1"

    # The switchable Anthropic path (used only when provider=anthropic).
    anthropic_parse_model: str = "claude-haiku-4-5"
    anthropic_draft_model: str = "claude-sonnet-5"

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
    # When true (or env JOBHUNTER_ATS_FIXTURES=1), ATS adapters navigate the
    # bundled local form fixtures instead of the live posting URL — a safe
    # offline demo of the fill + screenshot flow. Default false = real URLs.
    ats_demo_fixtures: bool = False
    # Never draft/apply to the same company again within this many days.
    company_dedupe_days: int = 30


class CapsConfig(BaseModel):
    emails_per_day: int = 25
    ats_submissions_per_day: int = 15
    applications_per_day: int = 30   # global cap across all channels
    per_company_per_day: int = 1


class SignatureConfig(BaseModel):
    name: str = "Vaishnavi"
    availability: str = "Available in 30 days (currently serving a 30-day notice period)."
    email: str = "singhvaishnavi258@gmail.com"
    resume_link: str = (
        "https://drive.google.com/file/d/1YbwTEkpjy1Y01MhIdrZUw9JWTXms7j-c/view?usp=sharing"
    )


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

    @property
    def groq_api_key(self) -> str | None:
        return os.getenv("GROQ_API_KEY") or None

    @property
    def resolved_provider(self) -> str | None:
        """Which LLM provider is actually usable now, or None (offline).

        Honors llm.provider, falling back to offline when the selected
        provider's key is absent.
        """
        p = self.llm.provider
        if p == "groq":
            return "groq" if self.groq_api_key else None
        if p == "anthropic":
            return "anthropic" if self.anthropic_api_key else None
        # auto
        if self.groq_api_key:
            return "groq"
        if self.anthropic_api_key:
            return "anthropic"
        return None


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
