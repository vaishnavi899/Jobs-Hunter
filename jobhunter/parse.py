"""Parse — turn a raw_post into a structured Job.

Two parsers behind one interface:

  LLMParser (spec default): resolve shortened links, fetch the destination page,
    then one Haiku call returning the strict JSON schema below. Retry once on
    malformed JSON, then quarantine to parse_failures rather than crashing.

  HeuristicParser (offline fallback + test engine): deterministic regex/keyword
    extraction — no API key, no billing. Used automatically when no
    ANTHROPIC_API_KEY is set, or when config `llm.engine` forces it. This is
    what lets `jobhunter parse` run end-to-end without secrets.

Salary is normalized to a single ANNUAL INR figure here (monthly stipends x12,
$ amounts x usd_to_inr) so filter.py compares like with like.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from .config import Config, load_config
from .store import (
    ApplyMethod,
    EmploymentType,
    Job,
    JobStatus,
    ParseFailure,
    RawPost,
    get_session,
    init_db,
)

# --------------------------------------------------------------------------- #
# Strict JSON schema (the Haiku contract, and the shape both parsers produce)  #
# --------------------------------------------------------------------------- #
PARSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "company": {"type": ["string", "null"]},
        "title": {"type": ["string", "null"]},
        "role_tier": {"type": ["integer", "null"], "enum": [1, 2, 3, None]},
        "employment_type": {
            "type": "string",
            "enum": ["fulltime", "internship", "contract", "unknown"],
        },
        "salary_min_inr": {"type": ["number", "null"]},
        "salary_max_inr": {"type": ["number", "null"]},
        "salary_unknown": {"type": "boolean"},
        "experience_required_years": {"type": ["number", "null"]},
        "location": {"type": ["string", "null"]},
        "remote": {"type": ["boolean", "null"]},
        "apply_method": {
            "type": "string",
            "enum": [
                "ats_greenhouse", "ats_lever", "ats_ashby", "ats_workable",
                "ats_workday", "email", "form", "dm", "unknown",
            ],
        },
        "apply_target": {"type": ["string", "null"]},
        "tech_stack": {"type": "array", "items": {"type": "string"}},
        "posted_by_handle": {"type": ["string", "null"]},
        "source_url": {"type": ["string", "null"]},
        "confidence": {"type": "number"},
    },
    "required": [
        "company", "title", "role_tier", "employment_type",
        "salary_min_inr", "salary_max_inr", "salary_unknown",
        "experience_required_years", "location", "remote",
        "apply_method", "apply_target", "tech_stack",
        "posted_by_handle", "source_url", "confidence",
    ],
}

_ENUM_FIELDS_DEFAULT = {
    "employment_type": "unknown",
    "apply_method": "unknown",
}


# --------------------------------------------------------------------------- #
# Reference data for the heuristic parser (India market)                       #
# --------------------------------------------------------------------------- #
INDIA_LOCATIONS = {
    "india", "bengaluru", "bangalore", "hyderabad", "pune", "delhi", "ncr",
    "gurugram", "gurgaon", "noida", "mumbai", "chennai", "ahmedabad", "kolkata",
    "jaipur", "indore", "chandigarh", "kochi", "coimbatore", "trivandrum",
    "thiruvananthapuram", "nagpur", "vizag", "visakhapatnam", "mysuru", "mysore",
}
FOREIGN_LOCATIONS = {
    "berlin", "london", "new york", "nyc", "san francisco", "bay area", "seattle",
    "singapore", "dubai", "abu dhabi", "toronto", "amsterdam", "paris", "dublin",
    "sydney", "tokyo", "austin", "boston", "remote us", "us only", "usa only",
}
REMOTE_MARKERS = ("remote", "work from home", "wfh", "anywhere in india")

ATS_DOMAINS = [
    ("boards.greenhouse.io", ApplyMethod.ats_greenhouse),
    ("greenhouse.io", ApplyMethod.ats_greenhouse),
    ("jobs.lever.co", ApplyMethod.ats_lever),
    ("lever.co", ApplyMethod.ats_lever),
    ("jobs.ashbyhq.com", ApplyMethod.ats_ashby),
    ("ashbyhq.com", ApplyMethod.ats_ashby),
    ("apply.workable.com", ApplyMethod.ats_workable),
    ("workable.com", ApplyMethod.ats_workable),
    ("myworkdayjobs.com", ApplyMethod.ats_workday),
    ("workday.com", ApplyMethod.ats_workday),
]

# skill -> canonical label; matched case-insensitively as whole-ish tokens
KNOWN_SKILLS = [
    "python", "c++", "c#", "javascript", "typescript", "java", "go", "rust",
    "fastapi", "flask", "django", "node", "node.js", "react", "next.js",
    "express", "rest", "graphql", "langchain", "rag", "llm", "genai",
    "prompt engineering", "fine-tuning", "chromadb", "vector", "embeddings",
    "mongodb", "mysql", "postgres", "postgresql", "redis", "sql", "nosql",
    "docker", "kubernetes", "aws", "gcp", "azure", "pytorch", "tensorflow",
    "machine learning", "ml", "nlp", "ci/cd", "git",
]

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_URL_RE = re.compile(r"https?://[^\s)]+", re.IGNORECASE)
_HANDLE_RE = re.compile(r"(?<!\w)@([A-Za-z0-9_]{2,30})")


# --------------------------------------------------------------------------- #
# Salary normalization -> annual INR                                           #
# --------------------------------------------------------------------------- #
def _num(s: str) -> float:
    return float(s.replace(",", "").strip())


def extract_salary(text: str, cfg: Config) -> tuple[Optional[float], Optional[float]]:
    """Return (min_annual_inr, max_annual_inr); either may be None.

    Handles: `8 LPA`, `₹8L`, `8-12 LPA`, `8,00,000`, `800000 per annum`,
    `up to 15 LPA`, `$120k`, `35k/month`, `₹35,000 pm`, `stipend 40000/month`.
    Monthly figures are annualized (x12); $ amounts converted at usd_to_inr.
    """
    t = text.lower()
    usd = cfg.salary.usd_to_inr

    def lakh_to_inr(x: float) -> float:
        return x * 100_000

    # Monthly stipend: "35k/month", "35,000 pm", "40000 per month", "₹35k/mo"
    m = re.search(
        r"(?:₹|rs\.?|inr)?\s*([\d,]+(?:\.\d+)?)\s*k?\s*(?:/|per\s*)?\s*(?:month|mo|pm)\b",
        t,
    )
    if m:
        raw = m.group(1)
        val = _num(raw)
        if "k" in t[m.start():m.end()]:
            val *= 1_000
        return (val * 12, val * 12)

    # Range in LPA/lakh: "8-12 LPA", "8 to 12 lakh"
    m = re.search(r"([\d.]+)\s*(?:-|to)\s*([\d.]+)\s*(?:lpa|lakhs?|l)\b", t)
    if m:
        return (lakh_to_inr(_num(m.group(1))), lakh_to_inr(_num(m.group(2))))

    # "up to 15 LPA" -> max only
    m = re.search(r"up\s*to\s*₹?\s*([\d.]+)\s*(?:lpa|lakhs?|l)\b", t)
    if m:
        return (None, lakh_to_inr(_num(m.group(1))))

    # Single LPA / lakh / ₹8L
    m = re.search(r"(?:₹\s*)?([\d.]+)\s*(?:lpa|lakhs?|l)\b", t)
    if m:
        v = lakh_to_inr(_num(m.group(1)))
        return (v, v)

    # Dollar amounts: "$120k", "$120,000"
    m = re.search(r"\$\s*([\d,]+(?:\.\d+)?)\s*(k)?", t)
    if m:
        v = _num(m.group(1)) * (1_000 if m.group(2) else 1)
        return (v * usd, v * usd)

    # Plain large INR: "8,00,000", "800000 per annum"
    m = re.search(r"(?:₹|rs\.?|inr)?\s*([\d,]{6,})\s*(?:per\s*annum|pa|/year|/yr|annually)?", t)
    if m:
        v = _num(m.group(1))
        if v >= 100_000:  # only treat clearly-annual-sized numbers as salary
            return (v, v)

    return (None, None)


# --------------------------------------------------------------------------- #
# Link resolution + page fetch (used by the LLM parser)                        #
# --------------------------------------------------------------------------- #
def resolve_and_fetch(url: str, cfg: Config) -> tuple[str, str]:
    """Follow redirects to the final URL and return (final_url, page_text).

    Best-effort: on any network/parse error returns (url, "").
    """
    try:
        import httpx
        from bs4 import BeautifulSoup

        timeout = cfg.llm.request_timeout_seconds
        with httpx.Client(follow_redirects=True, timeout=timeout,
                          headers={"User-Agent": "jobhunter/0.1"}) as client:
            resp = client.get(url)
            final_url = str(resp.url)
            soup = BeautifulSoup(resp.text, "html.parser")
            for tag in soup(["script", "style", "noscript"]):
                tag.decompose()
            text = re.sub(r"\s+", " ", soup.get_text(" ")).strip()
            return final_url, text[: cfg.llm.max_page_chars]
    except Exception:
        return url, ""


# --------------------------------------------------------------------------- #
# Parsers — one interface, provider chosen by config (Groq default / Anthropic  #
# switchable / offline heuristic). The prompt, schema, and link-fetch are       #
# provider-agnostic; only the API call differs.                                 #
# --------------------------------------------------------------------------- #
_PARSE_FIELDS = (
    "company (string|null), title (string|null), role_tier (1|2|3|null), "
    "employment_type (one of fulltime|internship|contract|unknown), "
    "salary_min_inr (number|null), salary_max_inr (number|null), "
    "salary_unknown (boolean), experience_required_years (number|null), "
    "location (string|null), remote (boolean|null), apply_method (one of "
    "ats_greenhouse|ats_lever|ats_ashby|ats_workable|ats_workday|email|form|dm|unknown), "
    "apply_target (string|null), tech_stack (array of strings), "
    "posted_by_handle (string|null), source_url (string|null), confidence (number 0..1)"
)


def build_parse_prompt(raw: RawPost, page_text: str) -> str:
    return (
        "Extract a structured job posting from this hiring post. Respond with a "
        f"single JSON object with EXACTLY these keys: {_PARSE_FIELDS}.\n"
        "Salary fields must be ANNUAL INR numbers (multiply monthly stipends by "
        "12; convert $ amounts at ~84 INR). If compensation is not stated, set "
        "salary_unknown=true and leave the numbers null. role_tier: 1 for "
        "SDE/Software Engineer/AI Engineer/Forward Deployed Engineer, 2 for "
        "adjacent engineering roles, 3 for product roles, null if unclear.\n\n"
        f"POST (@{raw.posted_by_handle or 'unknown'}):\n{raw.content}\n\n"
        f"LINKED PAGE:\n{page_text or '(none)'}"
    )


def _resolve_page(raw: RawPost, cfg: Config) -> tuple[str, str]:
    if cfg.llm.fetch_linked_pages:
        urls = _URL_RE.findall(raw.content)
        if urls:
            return resolve_and_fetch(urls[0], cfg)
    return "", ""


def _finish(data: dict, raw: RawPost, page_url: str) -> dict:
    data.setdefault("source_url", page_url or raw.source_url)
    data.setdefault("posted_by_handle", raw.posted_by_handle)
    return data


class GroqParser:
    """Parse via Groq's OpenAI-compatible API (JSON mode). Retry once, then raise
    so run_parse can degrade this item to the offline heuristic."""

    name = "groq"

    def __init__(self, cfg: Config):
        self.cfg = cfg
        from openai import OpenAI  # optional dep (uv sync --extra groq)

        self.client = OpenAI(
            base_url=cfg.llm.groq_base_url,
            api_key=cfg.groq_api_key,
            timeout=cfg.llm.request_timeout_seconds,
        )

    def parse(self, raw: RawPost) -> dict:
        page_url, page_text = _resolve_page(raw, self.cfg)
        prompt = build_parse_prompt(raw, page_text)
        last: Exception | None = None
        for _attempt in range(2):  # one retry on malformed JSON
            resp = self.client.chat.completions.create(
                model=self.cfg.llm.parse_model,
                temperature=0,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": "You output only a valid JSON object with the requested keys."},
                    {"role": "user", "content": prompt},
                ],
            )
            content = resp.choices[0].message.content or ""
            try:
                return _finish(json.loads(content), raw, page_url)
            except json.JSONDecodeError as exc:
                last = exc
        raise ValueError(f"Groq returned invalid JSON after retry: {last}")


class AnthropicParser:
    """Parse via Anthropic structured outputs (switchable; off by default)."""

    name = "anthropic"

    def __init__(self, cfg: Config):
        self.cfg = cfg
        import anthropic  # imported lazily

        self.client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)

    def parse(self, raw: RawPost) -> dict:
        page_url, page_text = _resolve_page(raw, self.cfg)
        resp = self.client.messages.create(
            model=self.cfg.llm.anthropic_parse_model,
            max_tokens=1024,
            output_config={"format": {"type": "json_schema", "schema": PARSE_SCHEMA}},
            messages=[{"role": "user", "content": build_parse_prompt(raw, page_text)}],
        )
        text = next((b.text for b in resp.content if b.type == "text"), "")
        return _finish(json.loads(text), raw, page_url)


class HeuristicParser:
    """Deterministic offline parser — no API key, no billing."""

    name = "heuristic"

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def parse(self, raw: RawPost) -> dict:
        text = raw.content or ""
        low = text.lower()

        title, tier = self._title_and_tier(text)
        employment = self._employment(low)
        smin, smax = extract_salary(text, self.cfg)
        salary_unknown = smin is None and smax is None
        exp = self._experience(low)
        location, remote = self._location(low)
        method, target = self._apply(text)
        stack = self._tech_stack(low)

        return {
            "company": self._company(text),
            "title": title,
            "role_tier": tier,
            "employment_type": employment,
            "salary_min_inr": smin,
            "salary_max_inr": smax,
            "salary_unknown": salary_unknown,
            "experience_required_years": exp,
            "location": location,
            "remote": remote,
            "apply_method": method,
            "apply_target": target,
            "tech_stack": stack,
            "posted_by_handle": raw.posted_by_handle,
            "source_url": raw.source_url or (_URL_RE.findall(text)[:1] or [None])[0],
            "confidence": 0.55,  # heuristic parses are lower-confidence by nature
        }

    # -- field extractors --------------------------------------------------- #
    def _title_and_tier(self, text: str) -> tuple[Optional[str], Optional[int]]:
        low = text.lower()
        for tier, key in ((1, "tier1"), (2, "tier2"), (3, "tier3")):
            for role in getattr(self.cfg.roles, key):
                if role.lower() in low:
                    return role, tier
        return None, None

    def _employment(self, low: str) -> str:
        if "intern" in low:
            return "internship"
        if "contract" in low or "freelance" in low:
            return "contract"
        if "full-time" in low or "full time" in low or "fulltime" in low:
            return "fulltime"
        return "unknown"

    def _experience(self, low: str) -> Optional[float]:
        # "3+ years", "minimum 2 years", "2-4 years", "5 yrs"
        m = re.search(r"(\d+)\s*(?:\+|to|-)?\s*\d*\s*(?:years?|yrs?)", low)
        if m:
            return float(m.group(1))
        if "fresher" in low or "no experience" in low or "0-1 year" in low:
            return 0.0
        return None

    def _location(self, low: str) -> tuple[Optional[str], Optional[bool]]:
        remote = any(mk in low for mk in REMOTE_MARKERS)
        for city in FOREIGN_LOCATIONS:
            if city in low:
                return city.title(), remote
        for city in INDIA_LOCATIONS:
            if re.search(rf"\b{re.escape(city)}\b", low):
                return city.title(), remote
        if remote:
            return "Remote", True
        return None, remote or None

    def _apply(self, text: str) -> tuple[str, Optional[str]]:
        for url in _URL_RE.findall(text):
            for domain, method in ATS_DOMAINS:
                if domain in url.lower():
                    return method.value, url
        email = _EMAIL_RE.search(text)
        if email and re.search(r"\b(email|mail|apply|send|dm your)\b", text, re.I):
            return "email", email.group(0)
        low = text.lower()
        if "dm to apply" in low or "dm me" in low or re.search(r"\bdm\b", low):
            return "dm", None
        urls = _URL_RE.findall(text)
        if urls:
            return "form", urls[0]
        if email:
            return "email", email.group(0)
        return "unknown", None

    def _tech_stack(self, low: str) -> list[str]:
        found = []
        for skill in KNOWN_SKILLS:
            if re.search(rf"(?<![\w+#]){re.escape(skill)}(?![\w+#])", low):
                found.append(skill)
        return found

    def _company(self, text: str) -> Optional[str]:
        # "at Acme", "@ Acme", "Company: Acme"
        m = re.search(r"\b(?:at|@)\s+([A-Z][A-Za-z0-9&.\- ]{1,40}?)(?:[,.\n(]|\s+(?:is|are|in|for|—|-)\b)", text)
        if m:
            return m.group(1).strip()
        m = re.search(r"company[:\-]\s*([A-Za-z0-9&.\- ]{2,40})", text, re.I)
        if m:
            return m.group(1).strip()
        return None


def _make_parser(provider: str, cfg: Config):
    """Construct a provider parser, or None if its client library is missing."""
    try:
        return GroqParser(cfg) if provider == "groq" else AnthropicParser(cfg)
    except Exception:  # noqa: BLE001 - missing/broken client lib -> caller degrades
        return None


def get_parser(cfg: Config):
    """Pick the parser: offline heuristic, or the resolved provider's LLM.

    Missing provider client libs never crash: `engine: auto` degrades to the
    offline heuristic; `engine: llm` raises a clear message.
    """
    engine = cfg.llm.engine
    if engine == "heuristic":
        return HeuristicParser(cfg)
    provider = cfg.resolved_provider
    if engine == "llm":
        if provider is None:
            raise RuntimeError(
                "llm.engine=llm but no provider key set (GROQ_API_KEY or ANTHROPIC_API_KEY)"
            )
        p = _make_parser(provider, cfg)
        if p is None:
            raise RuntimeError(
                f"provider '{provider}' selected but its client is not installed "
                f"(uv sync --extra {'groq' if provider == 'groq' else 'anthropic'})"
            )
        return p
    # auto: resolved provider if usable, else offline
    if provider in ("groq", "anthropic"):
        p = _make_parser(provider, cfg)
        if p is not None:
            return p
    return HeuristicParser(cfg)


# --------------------------------------------------------------------------- #
# Normalization + persistence                                                  #
# --------------------------------------------------------------------------- #
def _coerce(data: dict) -> dict:
    """Fill defaults and coerce enum-ish fields so a Job row can be built."""
    out = dict(data)
    for field, default in _ENUM_FIELDS_DEFAULT.items():
        val = out.get(field)
        out[field] = val if val else default
    if out.get("employment_type") not in EmploymentType.__members__:
        out["employment_type"] = "unknown"
    if out.get("apply_method") not in ApplyMethod.__members__:
        out["apply_method"] = "unknown"
    out.setdefault("tech_stack", [])
    return out


def build_job(raw: RawPost, data: dict) -> Job:
    d = _coerce(data)
    return Job(
        raw_post_id=raw.id,
        company=d.get("company"),
        title=d.get("title"),
        role_tier=d.get("role_tier"),
        employment_type=EmploymentType(d["employment_type"]),
        salary_min_inr=d.get("salary_min_inr"),
        salary_max_inr=d.get("salary_max_inr"),
        salary_unknown=bool(d.get("salary_unknown", True)),
        experience_required_years=d.get("experience_required_years"),
        location=d.get("location"),
        remote=d.get("remote"),
        apply_method=ApplyMethod(d["apply_method"]),
        apply_target=d.get("apply_target"),
        tech_stack=d.get("tech_stack") or [],
        confidence=d.get("confidence"),
        status=JobStatus.new,
    )


def run_parse(cfg: Config | None = None) -> dict:
    """Parse every raw_post that has no Job and no parse_failure yet.

    Retries the parse once on failure, then quarantines. Returns counts.
    """
    cfg = cfg or load_config()
    init_db(cfg)
    parser = get_parser(cfg)
    # The offline heuristic is always available as the per-item fallback.
    heuristic = parser if isinstance(parser, HeuristicParser) else HeuristicParser(cfg)
    parsed = quarantined = degraded = 0

    def _try(p, raw):
        try:
            return p.parse(raw), None
        except Exception as exc:  # noqa: BLE001 - never crash the batch
            return None, f"{type(exc).__name__}: {exc}"

    with get_session(cfg) as session:
        done_ids = {r for (r,) in session.query(Job.raw_post_id).all()}
        failed_ids = {
            r for (r,) in session.query(ParseFailure.raw_post_id).all() if r is not None
        }
        skip = done_ids | failed_ids
        posts = [p for p in session.query(RawPost).order_by(RawPost.id).all()
                 if p.id not in skip]

        for raw in posts:
            data, err = _try(parser, raw)
            if data is None and parser is not heuristic:
                # Provider failed (rate limit / HTTP / malformed / no client) ->
                # degrade THIS item to the offline heuristic. Never hard-fail.
                data, _ = _try(heuristic, raw)
                if data is not None:
                    degraded += 1
            if data is None:
                session.add(ParseFailure(raw_post_id=raw.id, error=err))
                quarantined += 1
                continue
            session.add(build_job(raw, data))
            parsed += 1
        session.commit()

    return {"parsed": parsed, "quarantined": quarantined,
            "degraded_to_offline": degraded, "engine": parser.name}
