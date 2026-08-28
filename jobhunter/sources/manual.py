"""Manual-intake source (v1 default).

No X automation, no scraping, no paid API. Vaishnavi forwards/pastes raw posts
into the watched inbox directory (or via `jobhunter paste`), and the pipeline
consumes them. Each file becomes one or more RawPostData:

  *.txt / *.eml / *.md  -> one post per file (whole body is the post content)
  *.json                -> a single post object or a list of them; recognized
                           keys: content/text, handle/posted_by_handle,
                           url/source_url, id/external_id

Archiving is a manual-source concern, kept off the Source protocol: ingest
calls `mark_consumed()` after it has committed the posts, and only then are the
originating files moved to inbox/_archived. A crash before commit leaves the
files in place, so nothing is lost.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..config import Config
from .base import RawPostData

_TEXT_SUFFIXES = {".txt", ".eml", ".md"}
_SOURCE_FILE_KEY = "_source_file"


class ManualIntakeSource:
    name = "manual"

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.inbox = Path(cfg.ingest.manual.inbox_dir)
        self.archive = self.inbox / "_archived"
        # Files touched by the most recent fetch_since, for mark_consumed().
        self._last_read_files: list[Path] = []

    def fetch_since(self, timestamp: Optional[datetime]) -> list[RawPostData]:
        self._last_read_files = []
        if not self.inbox.exists():
            return []
        posts: list[RawPostData] = []
        for path in sorted(self.inbox.iterdir()):
            if path.is_dir() or path.name.startswith("_") or path.name.startswith("."):
                continue
            file_posts = self._read_file(path)
            if file_posts:
                self._last_read_files.append(path)
                posts.extend(file_posts)
        return posts

    def mark_consumed(self) -> int:
        """Move the files read by the last fetch to inbox/_archived. Returns count.

        No-op when archiving is disabled in config. Safe to call after commit.
        """
        if not self.cfg.ingest.manual.archive_processed:
            return 0
        moved = 0
        self.archive.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        for path in self._last_read_files:
            if not path.exists():
                continue
            dest = self.archive / f"{stamp}_{path.name}"
            n = 1
            while dest.exists():
                dest = self.archive / f"{stamp}_{n}_{path.name}"
                n += 1
            path.rename(dest)
            moved += 1
        self._last_read_files = []
        return moved

    # -- readers ------------------------------------------------------------ #
    def _read_file(self, path: Path) -> list[RawPostData]:
        if path.suffix.lower() == ".json":
            return self._read_json(path)
        if path.suffix.lower() in _TEXT_SUFFIXES or path.suffix == "":
            text = path.read_text(encoding="utf-8", errors="replace").strip()
            if not text:
                return []
            return [
                RawPostData(
                    source=self.name,
                    content=text,
                    raw_payload={_SOURCE_FILE_KEY: path.name, "content": text},
                )
            ]
        return []

    def _read_json(self, path: Path) -> list[RawPostData]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
        items = data if isinstance(data, list) else [data]
        out: list[RawPostData] = []
        for obj in items:
            if not isinstance(obj, dict):
                continue
            content = obj.get("content") or obj.get("text") or ""
            if not str(content).strip():
                continue
            payload = dict(obj)
            payload[_SOURCE_FILE_KEY] = path.name
            out.append(
                RawPostData(
                    source=self.name,
                    content=str(content),
                    external_id=_str_or_none(obj.get("id") or obj.get("external_id")),
                    posted_by_handle=_str_or_none(
                        obj.get("handle") or obj.get("posted_by_handle")
                    ),
                    source_url=_str_or_none(obj.get("url") or obj.get("source_url")),
                    raw_payload=payload,
                )
            )
        return out


def _str_or_none(v) -> Optional[str]:
    return None if v is None else str(v)
