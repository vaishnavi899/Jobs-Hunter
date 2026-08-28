"""Ingest — pull from the active Source, dedupe, persist RawPost rows.

Checkpoint 2. Uses sources.get_source(cfg) so the concrete source (manual now,
paid X API later) is swappable via config with no change here.
"""

from __future__ import annotations

from .config import Config  # noqa: F401


def run_ingest(cfg: Config) -> int:
    """Fetch new posts, store de-duplicated RawPost rows, return count added.

    Implemented in Checkpoint 2 (Ingest + dedupe).
    """
    raise NotImplementedError("ingest is built in Checkpoint 2")
