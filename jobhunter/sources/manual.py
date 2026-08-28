"""Manual-intake source (v1 default).

No X automation, no scraping, no paid API. Vaishnavi forwards/pastes raw posts
into the watched inbox directory (or via `jobhunter paste`), and the pipeline
consumes them. Each file becomes one RawPostData:

  *.txt / *.eml  -> one post per file (whole body is the post content)
  *.json         -> either a single post object or a list of them; recognized
                    keys: content/text, handle/posted_by_handle, url/source_url,
                    id/external_id

Fully wiring this into ingest + dedupe is Checkpoint 2; this file already gives
that checkpoint a working reader.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..config import Config
from .base import RawPostData

_TEXT_SUFFIXES = {".txt", ".eml", ".md"}


class ManualIntakeSource:
    name = "manual"

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.inbox = Path(cfg.ingest.manual.inbox_dir)
        self.archive = self.inbox / "_archived"

    def fetch_since(self, timestamp: Optional[datetime]) -> list[RawPostData]:
        if not self.inbox.exists():
            return []
        posts: list[RawPostData] = []
        for path in sorted(self.inbox.iterdir()):
            if path.is_dir() or path.name.startswith("_") or path.name.startswith("."):
                continue
            posts.extend(self._read_file(path))
        return posts

    def _read_file(self, path: Path) -> list[RawPostData]:
        if path.suffix.lower() == ".json":
            return self._read_json(path)
        if path.suffix.lower() in _TEXT_SUFFIXES or path.suffix == "":
            text = path.read_text(encoding="utf-8", errors="replace").strip()
            if not text:
                return []
            return [RawPostData(source=self.name, content=text, source_url=None,
                                raw_payload={"filename": path.name})]
        return []

    def _read_json(self, path: Path) -> list[RawPostData]:
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data if isinstance(data, list) else [data]
        out: list[RawPostData] = []
        for obj in items:
            if not isinstance(obj, dict):
                continue
            content = obj.get("content") or obj.get("text") or ""
            if not content:
                continue
            out.append(
                RawPostData(
                    source=self.name,
                    content=str(content),
                    external_id=_str_or_none(obj.get("id") or obj.get("external_id")),
                    posted_by_handle=_str_or_none(obj.get("handle") or obj.get("posted_by_handle")),
                    source_url=_str_or_none(obj.get("url") or obj.get("source_url")),
                    raw_payload=obj,
                )
            )
        return out


def _str_or_none(v) -> Optional[str]:
    return None if v is None else str(v)
