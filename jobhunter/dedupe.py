"""Dedupe — content-hash + per-company 30-day rules. Wired in Checkpoint 2.

Two layers:
  1. Ingest dedupe: skip any RawPost whose content_hash was already stored.
  2. Apply dedupe: never apply to the same company twice within 30 days.
"""

from __future__ import annotations

import hashlib
import re


def content_hash(text: str) -> str:
    """Stable dedupe key: lowercased, whitespace-collapsed sha256."""
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
