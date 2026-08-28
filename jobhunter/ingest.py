"""Ingest — pull from the active Source, dedupe, persist RawPost rows.

Source-agnostic: it only knows the `Source` protocol (fetch_since), so the
concrete source (manual intake now, a paid X API later) is swapped via config
with no change here.

Dedupe is by content_hash: a post already stored is skipped, which is what
makes re-ingesting the same content a no-op. The original payload is never
discarded — the full source object is stored in raw_posts.raw_payload.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .config import Config, load_config
from .dedupe import already_seen, content_hash
from .sources import get_source
from .sources.base import RawPostData
from .store import RawPost, get_session, init_db


@dataclass
class IngestResult:
    fetched: int = 0
    added: int = 0
    skipped_duplicate: int = 0
    archived: int = 0

    def __str__(self) -> str:
        return (
            f"fetched={self.fetched} added={self.added} "
            f"skipped_duplicate={self.skipped_duplicate} archived={self.archived}"
        )


def _to_row(post: RawPostData, hash_: str) -> RawPost:
    posted_at = post.posted_at if isinstance(post.posted_at, datetime) else None
    row = RawPost(
        source=post.source,
        external_id=post.external_id,
        posted_by_handle=post.posted_by_handle,
        source_url=post.source_url,
        content=post.content,
        raw_payload=post.raw_payload or {},
        content_hash=hash_,
    )
    if posted_at is not None:
        row.ingested_at = posted_at
    return row


def run_ingest(cfg: Config | None = None, since: datetime | None = None) -> IngestResult:
    """Fetch new posts from the active source, store de-duplicated RawPost rows.

    Dedupe happens both against the DB and within this batch (two identical
    posts pasted at once collapse to one row). Files are archived only after a
    successful commit, so a crash never loses an unprocessed post.
    """
    cfg = cfg or load_config()
    init_db(cfg)
    source = get_source(cfg)
    posts = source.fetch_since(since)

    result = IngestResult(fetched=len(posts))
    seen_this_batch: set[str] = set()

    with get_session(cfg) as session:
        for post in posts:
            h = content_hash(post.content)
            if h in seen_this_batch or already_seen(session, h):
                result.skipped_duplicate += 1
                continue
            session.add(_to_row(post, h))
            seen_this_batch.add(h)
            result.added += 1
        session.commit()

    # Archive originals only after the commit succeeded (manual source only).
    mark_consumed = getattr(source, "mark_consumed", None)
    if callable(mark_consumed):
        result.archived = mark_consumed()

    return result
