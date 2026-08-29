"""Workable ATS adapter (Playwright) — approval-gated, dry-run by default.

Thin adapter on the shared engine (submit/_ats.py): selectors target the common
Workable-hosted form (first/last name, email, phone, a resume file input, a
summary/cover-letter textarea). Real forms add custom required questions; those
are detected by the engine's live-DOM required-field scan and surfaced as
needs_review, never guessed. dry_run writes a field-map .json + review
screenshot to ./outbox/ and never clicks submit.
"""

from __future__ import annotations

from ..store import ApplyMethod
from ._ats import AtsAdapterBase, fill_first, upload_resume


class WorkableAdapter(AtsAdapterBase):
    method = ApplyMethod.ats_workable
    ats = "workable"
    fixture = "workable_form.html"
    submit_selector = (
        "button[type=submit], button:has-text('Submit application'), "
        "button[data-ui='submit-application']"
    )

    def fill(self, page, f: dict) -> None:
        fill_first(page,
                   "input[name*='firstname' i], input[name='candidate[firstname]'], "
                   "input[autocomplete='given-name']",
                   f["first_name"] or f["name"])
        fill_first(page,
                   "input[name*='lastname' i], input[name='candidate[lastname]'], "
                   "input[autocomplete='family-name']",
                   f["last_name"])
        fill_first(page,
                   "input[name*='email' i], input[type='email']",
                   f["email"])
        fill_first(page,
                   "input[name*='phone' i], input[type='tel']",
                   f["phone"])
        fill_first(page,
                   "textarea[name*='summary' i], textarea[name*='cover' i], textarea",
                   f["cover_letter"])
        upload_resume(page, f["resume_upload"])
