"""Draft — one tailored outreach message per surviving job.

The message follows Vaishnavi's personal outreach template:

  1. Opener — her intro (recent 2026 CS grad from JIIT Noida, now AI SDE at
     Deepreef).
  2. Skills line — her stated stack.
  3. Middle — ONE custom JD-specific hook paragraph (what the role does / a
     requirement she matches). Never addressed to a person.
  4. Link block — Resume Link, Job link, Email id.
  5. Close — "I'll be looking forward to your response. Thank you!" + her name.

Two drafters behind one interface (same pattern as parse):

  LLMDrafter (spec default): one Sonnet call writes the prose (opener -> skills
    -> JD hook -> [relocation] -> availability), 150-200 words. The link block
    and close are appended deterministically so the resume link / job link /
    email are always exact.

  TemplateDrafter (offline fallback + test engine): deterministic, no API key,
    no billing. Used automatically when no ANTHROPIC_API_KEY is set.

Hard requirements (both engines):
  - Always state availability as "available in 30 days" — never imply immediate
    availability (Vaishnavi is employed, serving a 30-day notice).
  - For an onsite role outside Delhi NCR, state plainly she will relocate; do
    NOT include the line for Delhi NCR roles.
  - Contact email from config as the from/reply-to identity; no filler.

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

AVAILABILITY = "available in 30 days"

# Fixed opener + skills line from Vaishnavi's template.
OPENER = (
    "I'm Vaishnavi, a recent 2026 Computer Science graduate from Jaypee "
    "Institute of Information Technology (JIIT), Noida, currently working as an "
    "AI SDE at Deepreef."
)
SKILLS_LINE = (
    "My core strengths span AI/ML, Data Structures & Algorithms, SQL, Python, "
    "C++, and Java, along with extensive web development — all backed by "
    "hands-on projects and hackathons."
)
CLOSE = "I'll be looking forward to your response. Thank you!"

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


def job_link(job: Job) -> str:
    """The source post / JD URL for this job (for the 'Job link' line)."""
    if job.raw_post and job.raw_post.source_url:
        return job.raw_post.source_url
    if job.apply_target and job.apply_target.startswith("http"):
        return job.apply_target
    return "(job post link)"


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


def links_and_close(job: Job, cfg: Config, resume: dict) -> str:
    """The trailing link block + warm close — identical shape for both engines."""
    name = resume.get("name") or cfg.signature.name
    return "\n".join([
        f"Resume Link: {cfg.signature.resume_link}",
        f"Job link: {job_link(job)}",
        f"Email id: {cfg.signature.email}",
        "",
        CLOSE,
        name,
    ])


def assemble(prose: str, job: Job, cfg: Config, resume: dict) -> str:
    return f"{prose}\n\n{links_and_close(job, cfg, resume)}"


class TemplateDrafter:
    name = "template"

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

    # Concrete project to name in the hook, keyed by the skills a job asks for.
    _PROJECT_HOOKS = [
        ({"langchain", "rag", "chromadb", "vector", "llm", "ml", "fine-tuning",
          "prompt-engineering"},
         "I built Campus Companion, a local-first RAG assistant (LangChain + "
         "ChromaDB, 85% retrieval confidence), and run large-scale prompt tuning "
         "over LLM workflows at Deepreef"),
        ({"react", "node", "javascript", "typescript", "next", "express",
          "mongodb", "mysql"},
         "I've shipped full-stack MERN features — a college portal with "
         "attendance, dashboards, and an AI help assistant — alongside "
         "FastAPI/Flask services"),
        ({"c++", "c", "go", "rust"},
         "I engineered a multi-strategy CPU scheduler in C on Linux (FCFS, Round "
         "Robin, Priority) with IPC across 50+ processes"),
    ]

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def draft(self, job: Job, jd_text: str, resume: dict) -> dict:
        name = resume.get("name") or self.cfg.signature.name
        title = job.title or "the role"

        resume_canon = canonical_skills(resume.get("skills_flat", []))
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
            "I ship production LLM systems end to end at Deepreef",
        )

        subject = f"{title} — {name} (available in 30 days)"

        # JD-specific hook (no person addressed). Replaces the old
        # "I came across your profile..." line.
        if matched_display:
            hook = (
                f"Your {title} role calls for {specific}, which is exactly what I "
                f"work on: {project}. My hands-on stack lines up closely with what "
                f"you listed — {', '.join(matched_display[:5])} — so I can "
                f"contribute from day one."
            )
        else:
            hook = (
                f"Your {title} role is a strong fit for what I work on: {project}. "
                f"I keep changes tightly scoped and hand work off cleanly for review."
            )

        parts = [
            "Hi team,",
            "",
            OPENER,
            SKILLS_LINE,
            hook,
        ]
        if needs_relocation(job):
            parts.append(
                f"This role is based in {job.location}, outside my Delhi NCR base — "
                f"I'm glad to relocate there and am open to any location in India."
            )
        parts.append(
            f"I'm currently employed and can join within 30 days ({AVAILABILITY})."
        )
        prose = "\n".join(parts)

        return {
            "subject": subject,
            "body": assemble(prose, job, self.cfg, resume),
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
            f"include one sentence that she is glad to relocate and is open to any "
            f"location in India."
            if needs_relocation(job)
            else "Do NOT mention relocation."
        )
        prompt = (
            "Write the PROSE of a tailored outreach message for this candidate, "
            "following her template EXACTLY in this order:\n"
            f"1. Opener (use verbatim): \"{OPENER}\"\n"
            f"2. Skills line (use verbatim or lightly adapted): \"{SKILLS_LINE}\"\n"
            "3. ONE custom paragraph: a JD-specific hook tied to what THIS role "
            "does or a concrete requirement she matches — reference something "
            "specific from the job. Do NOT address a person; do NOT write 'I came "
            "across your profile'. No filler, no 'I am writing to express my "
            "interest.'\n"
            f"4. {relocation}\n"
            "5. State availability as 'available in 30 days' — never imply she can "
            "start immediately.\n"
            "Keep the PROSE (steps 1-5) between 150 and 200 words. Do NOT write a "
            "greeting line, the resume/job/email link block, or a sign-off — those "
            "are added separately. Also return a subject line and a reordered "
            "top-6 list of her resume bullets, most relevant first.\n\n"
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
        prose = "Hi team,\n\n" + data["cover_note"].strip()
        return {
            "subject": data["subject"],
            "body": assemble(prose, job, self.cfg, resume),
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
