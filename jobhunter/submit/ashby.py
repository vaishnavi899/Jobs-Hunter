"""Ashby ATS adapter (Playwright) — approval-gated, dry-run by default.

Thin adapter on the shared engine (submit/_ats.py): selectors target the common
Ashby-hosted form (system fields for name/email/phone, a resume file input, a
cover-letter textarea). Real forms add custom required questions; those are
detected by the engine's live-DOM required-field scan and surfaced as
needs_review, never guessed. dry_run writes a field-map .json + review
screenshot to ./outbox/ and never clicks submit.
"""

from __future__ import annotations

from ..store import ApplyMethod
from ._ats import AtsAdapterBase, fill_first, upload_resume


class AshbyAdapter(AtsAdapterBase):
    method = ApplyMethod.ats_ashby
    ats = "ashby"
    fixture = "ashby_form.html"
    submit_selector = (
        "button[type=submit], button:has-text('Submit Application'), "
        ".ashby-application-form-submit-button"
    )

    def fill(self, page, f: dict) -> None:
        fill_first(page,
                   "input[name='_systemfield_name'], input[aria-label='Name'], "
                   "input[name*='name' i]:not([name*='first' i]):not([name*='last' i])",
                   f["name"])
        fill_first(page,
                   "input[name='_systemfield_email'], input[type='email'], "
                   "input[aria-label='Email']",
                   f["email"])
        fill_first(page,
                   "input[name='_systemfield_phone'], input[type='tel'], "
                   "input[aria-label*='Phone' i]",
                   f["phone"])
        fill_first(page,
                   "textarea[aria-label*='cover' i], textarea[name*='cover' i], textarea",
                   f["cover_letter"])
        upload_resume(page, f["resume_upload"])
