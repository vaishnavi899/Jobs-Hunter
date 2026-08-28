"""The Adapter protocol — the seam that keeps submission channel-agnostic.

Every channel (email now; Greenhouse/Lever/Ashby/Workable/generic form/DM
later) implements the same two-method contract:

  dry_run(job, application, cfg) -> SubmitResult   # writes an artifact to
                                                    # ./outbox/, sends NOTHING
  send(job, application, cfg)    -> SubmitResult    # the real send, only ever
                                                    # reached behind an explicit
                                                    # human approval gate

Dry-run is the default everywhere. `send` is never called by the normal
pipeline — the CLI reaches it only with `--send --i-confirm`, and even then
only when the job has a real recipient and credentials exist.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable

from ..store import ApplyMethod


@dataclass
class SubmitResult:
    """Outcome of a dry-run or (gated) real submission attempt."""

    action: str  # "dry_run" | "sent" | "skipped" | "needs_human" | "needs_auth" | "failed"
    ok: bool = True
    outbox_path: Optional[str] = None
    external_ref: Optional[str] = None
    recipient: Optional[str] = None
    error: Optional[str] = None
    note: Optional[str] = None


@runtime_checkable
class Adapter(Protocol):
    method: ApplyMethod

    def dry_run(self, job, application, cfg) -> SubmitResult:
        """Write a ready-to-send artifact to ./outbox/. Transmit nothing."""
        ...

    def send(self, job, application, cfg) -> SubmitResult:
        """Real send — only reached behind the explicit approval gate."""
        ...
