"""Lever ATS adapter (Playwright) — approval-gated, dry-run by default.

Selectors target the common Lever-hosted form (a single full-name field,
email, phone, a resume file input, an "additional information" textarea). Real
forms vary and may add required custom questions; those are detected by the
shared engine's required-field scan and surfaced as needs_review rather than
guessed. dry_run writes a field-map .json + review screenshot to ./outbox/ and
never clicks submit; the real submit is only reached via send() when validation
is clean.
"""

from __future__ import annotations

from ..store import ApplyMethod
from . import _ats
from .base import SubmitResult


def _fill(page, selector: str, value: str) -> None:
    if not value:
        return
    el = page.query_selector(selector)
    if el:
        try:
            el.fill(value)
        except Exception:
            pass


class LeverAdapter:
    method = ApplyMethod.ats_lever
    ats = "lever"
    fixture = "lever_form.html"
    submit_selector = ".template-btn-submit, button[type=submit], button:has-text('Submit application')"

    def resolve_url(self, job) -> str:
        if job.apply_target and job.apply_target.startswith("http"):
            return job.apply_target
        if job.raw_post and job.raw_post.source_url:
            return job.raw_post.source_url
        return ""

    def fill(self, page, f: dict) -> None:
        _fill(page, "input[name='name'], input[autocomplete='name']", f["name"])
        _fill(page, "input[name='email'], input[type='email']", f["email"])
        _fill(page, "input[name='phone'], input[type='tel']", f["phone"])
        _fill(page, "textarea[name='comments'], textarea", f["cover_letter"])
        if f["resume_upload"]:
            fi = page.query_selector("input[type='file'], input[name='resume']")
            if fi:
                try:
                    fi.set_input_files(f["resume_upload"])
                except Exception:
                    pass

    def dry_run(self, job, application, cfg) -> SubmitResult:
        from ..config import load_resume
        return _ats.execute(self, job, application, cfg, load_resume() or {}, do_click=False)

    def send(self, job, application, cfg) -> SubmitResult:
        from ..config import load_resume
        return _ats.execute(self, job, application, cfg, load_resume() or {}, do_click=True)
