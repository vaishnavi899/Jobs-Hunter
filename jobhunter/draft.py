"""Draft — one Sonnet call per surviving job.

Checkpoint 4. Produces subject, 150-200 word cover note referencing the JD,
and a reordered top-6 resume bullet list. Always states 30-day availability;
states relocation willingness for onsite roles outside Delhi NCR. Drafts are
cached so a retry does not re-bill.
"""

from __future__ import annotations


def draft_application(job, cfg, resume) -> dict:
    """Return {subject, body, resume_bullets}. Built in Checkpoint 4."""
    raise NotImplementedError("draft is built in Checkpoint 4")
