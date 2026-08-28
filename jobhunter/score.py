"""Score — 0-100 fit ranking, weights from config.yaml.

Checkpoint 4. Location is flat (no city preference); tech-stack overlap against
profile/resume.json skills is the main tiebreaker.
"""

from __future__ import annotations


def score_job(job, cfg, resume) -> tuple[int, dict]:
    """Return (score, breakdown). Built in Checkpoint 4."""
    raise NotImplementedError("score is built in Checkpoint 4")
