"""Parse — resolve links, fetch JD, one Haiku call -> strict JSON -> Job.

Checkpoint 3. Retry once on malformed JSON, then quarantine to parse_failures
rather than crashing.
"""

from __future__ import annotations


def run_parse(cfg) -> int:
    """Parse un-parsed RawPosts into Job rows. Returns number parsed."""
    raise NotImplementedError("parse is built in Checkpoint 3")
