"""Ingestion sources. Pick the active one from config `ingest.active_source`."""

from __future__ import annotations

from ..config import Config
from .base import RawPostData, Source
from .manual import ManualIntakeSource

__all__ = ["RawPostData", "Source", "get_source"]

_REGISTRY = {
    "manual": ManualIntakeSource,
    # "x_api": XApiSource,   # drop-in later behind the same protocol; no pipeline change
}


def get_source(cfg: Config) -> Source:
    name = cfg.ingest.active_source
    try:
        return _REGISTRY[name](cfg)
    except KeyError as exc:
        raise ValueError(
            f"Unknown ingest source {name!r}. Known: {sorted(_REGISTRY)}"
        ) from exc
