"""The Source protocol — the seam that keeps the pipeline source-agnostic.

parse/filter/score/draft never import a concrete source; they consume
RawPostData. A future paid X/Twitter API source implements the same
`fetch_since` contract and drops into the registry with zero pipeline change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Protocol, runtime_checkable


@dataclass
class RawPostData:
    """A post as it arrives from a source, before it becomes a store.RawPost row."""

    source: str
    content: str
    external_id: Optional[str] = None
    posted_by_handle: Optional[str] = None
    source_url: Optional[str] = None
    posted_at: Optional[datetime] = None
    raw_payload: dict = field(default_factory=dict)


@runtime_checkable
class Source(Protocol):
    """Any ingestion source. The one method the pipeline depends on."""

    name: str

    def fetch_since(self, timestamp: Optional[datetime]) -> list[RawPostData]:
        """Return posts newer than `timestamp` (all posts when None)."""
        ...
