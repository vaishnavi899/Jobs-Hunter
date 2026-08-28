"""Draft — one tailored application per surviving job.

Two drafters behind one interface (same pattern as parse):

  LLMDrafter (spec default): one Sonnet call per job. Inputs: resume.json, the
    full JD text, and the original post. Output: subject line, a 150-200 word
    cover note referencing something specific from the JD, and a reordered
    top-6 resume bullet list.

  TemplateDrafter (offline fallback + test engine): deterministic, no API key,
    no billing. Used automatically when no ANTHROPIC_API_KEY is set.

Hard requirements (both engines):
  - Always state availability as "available in 30 days" — never imply immediate
    availability (Vaishnavi is employed, serving a 30-day notice).
  - For an onsite role outside Delhi NCR, state plainly she will relocate.
  - Use the configured contact email; no filler, no "I am writing to express
    my interest."

Drafts are cached as dry-run Application rows: a job that already has one is
skipped, so a re-run never re-bills. NOTHING is sent here — submission adapters
are later checkpoints.
"""

from __future__ import annotations

import re

from .config import Config, load_config, load_resume
from .score import _canon, canonical_skills
from .store import (
    Application,
    ApplicationStatus,
    Job,
    JobStatus,
    get_session,
    init_db,
)

CONTACT_EMAIL = "singhvaishnavi258@gmail.com"
AVAILABILITY = "available in 30 days"

DELHI_NCR = {
    "delhi", "new delhi", "ncr", "gurugram", "gurgaon",
    "noida", "ghaziabad", "faridabad",
}


def is_delhi_ncr(location: str | None) -> bool:
    loc = (location or "").lower()
    return any(re.search(rf"\b{re.escape(c)}\b", loc) for c in DELHI_NCR)


def needs_relocation(job: Job) -> bool:
    """Onsite role at a known location outside Delhi NCR -> mention relocation.

    Remote roles and unknown locations don't (nothing to relocate for / can't
    assert). Base-region roles (Delhi NCR) don't need it.
    """
    if job.remote:
        return False
    if not job.location:
        return False
    return not is_delhi_ncr(job.location)


def _all_bullets(resume: dict) -> list[str]:
    out: list[str] = []
    for exp in resume.get("experience", []):
        out.extend(exp.get("bullets", []))
    for proj in resume.get("projects", []):
        out.extend(proj.get("bullets", []))
    return out


def top_bullets(job: Job, resume: dict, n: int = 6) -> list[str]:
    """Rank resume bullets by canonical-skill overlap with the job; top n, stable."""
    job_skills = canonical_skills(job.tech_stack)
    bullets = _all_bullets(resume)

    def overlap(b: str) -> int:
        toks = canonical_skills(re.findall(r"[A-Za-z][A-Za-z0-9.+#\-]+", b))
        return len(toks & job_skills)

    ranked = sorted(range(len(bullets)), key=lambda i: (-overlap(bullets[i]), i))
    return [bullets[i] for i in ranked[:n]]


class TemplateDrafter:
    name = "template"

    def __init__(self, cfg: Config):
        self.cfg = cfg

    # Concrete project to name, keyed by the skill families a job asks for.
    _PROJECT_HOOKS = [
        ({"langchain", "rag", "chromadb", "vector", "llm", "ml", "fine-tuning",
          "prompt-engineering"},
         "building Campus Companion, a local-first RAG assistant (LangChain + "
         "ChromaDB, 85% retrieval confidence) and running large-scale prompt "
         "tuning over LLM workflows at Deepreef"),
        ({"react", "node", "javascript", "typescript", "next", "express",
          "mongodb", "mysql"},
         "shipping full-stack MERN features — a college portal with attendance, "
         "dashboards, and an AI help assistant — plus FastAPI/Flask services"),
        ({"c++", "c", "go", "rust"},
         "engineering a multi-strategy CPU scheduler in C on Linux (FCFS, Round "
         "Robin, Priority) with IPC across 50+ processes"),
    ]

    # Pretty display labels for canonical skill tokens.
    _PRETTY = {
        "node": "Node.js", "react": "React", "javascript": "JavaScript",
        "typescript": "TypeScript", "fastapi": "FastAPI", "flask": "Flask",
        "mongodb": "MongoDB", "mysql": "MySQL", "postgres": "Postgres",
        "rag": "RAG", "llm": "LLMs", "langchain": "LangChain", "chromadb": "ChromaDB",
        "python": "Python", "c++": "C++", "c#": "C#", "ml": "ML", "rest": "REST APIs",
        "docker": "Docker", "kubernetes": "Kubernetes", "aws": "AWS", "gcp": "GCP",
        "next": "Next.js", "express": "Express", "prompt-engineering": "prompt engineering",
        "fine-tuning": "LLM fine-tuning", "vector": "vector search", "nlp": "NLP",
    }

    def draft(self, job: Job, jd_text: str, resume: dict) -> dict:
        name = resume.get("name", "Vaishnavi")
        company = job.company or "your team"
        title = job.title or "the role"
        resume_canon = canonical_skills(resume.get("skills_flat", []))
        # Canonical matched skills, deduped, in the order the JD listed them.
        matched_canon: list[str] = []
        for t in (job.tech_stack or []):
            c = _canon(t)
            if c in resume_canon and c not in matched_canon:
                matched_canon.append(c)
        matched_display = [self._PRETTY.get(c, c) for c in matched_canon]
        matched_canon_set = set(matched_canon)
        specific = matched_display[0] if matched_display else (
            job.tech_stack[0] if job.tech_stack else title
        )
        project = next(
            (blurb for skills, blurb in self._PROJECT_HOOKS if matched_canon_set & skills),
            "shipping production LLM systems end to end at Deepreef",
        )

        subject = f"{title} — {name} (available in 30 days)"

        paras = [f"Hi {company} team,", ""]
        paras.append(
            f"I'd like to be considered for {title}. Your post calls for "
            f"{specific}, which is exactly the work I do as an SDE-AI at Deepreef, "
            f"where I take AI features from research and prototyping through to "
            f"production deployment."
        )
        if matched_display:
            paras.append(
                f"My hands-on stack lines up closely with what you listed — "
                f"{', '.join(matched_display[:5])}. Recent work includes "
                f"{project}. I care about correctness, keep changes tightly "
                f"scoped, and hand work off cleanly for review."
            )
        else:
            paras.append(
                f"I bring production experience across Python, FastAPI/Flask, "
                f"React/Node, and applied LLM/RAG work — recently {project}. I "
                f"care about correctness and keep changes tightly scoped."
            )
        paras.append(
            "Alongside that I've placed in the top 4% at Myntra HackerRamp and "
            "solved 400+ DSA problems, so I move quickly without cutting corners."
        )
        if needs_relocation(job):
            paras.append(
                f"This role is based in {job.location}, outside my Delhi NCR base — "
                f"I'm glad to relocate there and am open to any location in India."
            )
        paras.append(
            f"I'm currently employed and can join within 30 days ({AVAILABILITY}). "
            f"My resume is attached; you can reach me at {CONTACT_EMAIL}."
        )
        paras.append("")
        paras.append(name)

        return {
            "subject": subject,
            "body": "\n".join(paras),
            "resume_bullets": top_bullets(job, resume),
        }


class LLMDrafter:
    name = "llm"

    def __init__(self, cfg: Config):
        self.cfg = cfg
        import anthropic

        self.client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)

    _SCHEMA = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "subject": {"type": "string"},
            "cover_note": {"type": "string"},
            "resume_bullets": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["subject", "cover_note", "resume_bullets"],
    }

    def draft(self, job: Job, jd_text: str, resume: dict) -> dict:
        import json

        relocation = (
            f"This role is onsite in {job.location}, outside her Delhi NCR base — "
            f"state plainly that she is willing to relocate."
            if needs_relocation(job)
            else "Do not mention relocation."
        )
        prompt = (
            "Write a tailored job application for this candidate. Requirements:\n"
            "- A subject line.\n"
            "- A 150-200 word cover note that references something SPECIFIC from "
            "the job description. No filler; never write 'I am writing to express "
            "my interest.'\n"
            "- State availability as 'available in 30 days'. Never imply she can "
            "start immediately.\n"
            f"- {relocation}\n"
            f"- Sign off with her name and contact email {CONTACT_EMAIL}.\n"
            "- A reordered top-6 list of her resume bullets, most relevant first.\n\n"
            f"RESUME (JSON):\n{json.dumps(resume)}\n\n"
            f"JOB (company={job.company}, title={job.title}, location={job.location}, "
            f"remote={job.remote}):\n{jd_text}"
        )
        resp = self.client.messages.create(
            model=self.cfg.llm.draft_model,
            max_tokens=1500,
            output_config={"format": {"type": "json_schema", "schema": self._SCHEMA}},
            messages=[{"role": "user", "content": prompt}],
        )
        text = next((b.text for b in resp.content if b.type == "text"), "")
        data = json.loads(text)
        return {
            "subject": data["subject"],
            "body": data["cover_note"],
            "resume_bullets": data["resume_bullets"],
        }


def get_drafter(cfg: Config):
    engine = cfg.llm.engine
    if engine == "heuristic":  # heuristic parse -> template draft (offline pair)
        return TemplateDrafter(cfg)
    if engine == "llm":
        if not cfg.anthropic_api_key:
            raise RuntimeError("llm.engine=llm but ANTHROPIC_API_KEY is not set")
        return LLMDrafter(cfg)
    return LLMDrafter(cfg) if cfg.anthropic_api_key else TemplateDrafter(cfg)


def run_draft(cfg: Config | None = None, limit: int | None = None) -> dict:
    """Draft the highest-scoring scored jobs that don't already have a draft.

    Caches each draft as a dry-run Application row (nothing is sent).
    """
    cfg = cfg or load_config()
    init_db(cfg)
    resume = load_resume()
    if resume is None:
        return {"drafted": 0, "engine": None, "error": "profile/resume.json not found"}

    drafter = get_drafter(cfg)
    drafted = skipped = 0
    with get_session(cfg) as session:
        already = {a for (a,) in session.query(Application.job_id).all()}
        q = (
            session.query(Job)
            .filter(Job.status.in_([JobStatus.scored, JobStatus.drafted]))
            .order_by(Job.fit_score.desc().nullslast())
        )
        jobs = [j for j in q.all() if j.id not in already]
        if limit is not None:
            jobs = jobs[:limit]

        for job in jobs:
            jd_text = job.raw_post.content if job.raw_post else ""
            try:
                d = drafter.draft(job, jd_text, resume)
            except Exception as exc:  # noqa: BLE001 - never crash the batch
                skipped += 1
                job.status = JobStatus.needs_human
                job.filter_reason = f"draft failed: {type(exc).__name__}: {exc}"
                continue
            session.add(
                Application(
                    job_id=job.id,
                    channel=job.apply_method,
                    status=ApplicationStatus.dry_run,
                    dry_run=True,
                    subject=d["subject"],
                    body=d["body"],
                    resume_bullets=d["resume_bullets"],
                )
            )
            job.status = JobStatus.drafted
            drafted += 1
        session.commit()

    return {"drafted": drafted, "skipped": skipped, "engine": drafter.name}
