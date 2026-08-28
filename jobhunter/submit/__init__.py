"""Submission adapters + router.

APPROVAL-GATED, dry-run by default (resolved decision 2). Nothing sends
automatically in v1. Every drafted application is written to ./outbox/ and
requires explicit per-application human approval before any adapter sends.
Channels graduate to auto-send only via config `submission.auto_send_channels`.

The router picks an adapter from job.apply_method. Adapters are built across
Checkpoints 5-7; this module defines the shared contract and the gate.
"""

from __future__ import annotations

from ..store import ApplyMethod

__all__ = ["get_adapter", "SubmitResult"]


class SubmitResult:
    """Outcome of a (real or dry-run) submission attempt."""

    def __init__(self, ok: bool, status: str, external_ref: str | None = None,
                 screenshot: str | None = None, error: str | None = None):
        self.ok = ok
        self.status = status              # submitted | needs_human | failed | dry_run
        self.external_ref = external_ref
        self.screenshot = screenshot
        self.error = error


def get_adapter(method: "ApplyMethod"):
    """Return the adapter for an apply method. Populated in Checkpoints 5-7."""
    from . import email, greenhouse, lever, ashby, workable, generic_form

    registry = {
        ApplyMethod.email: email.EmailAdapter,
        ApplyMethod.ats_greenhouse: greenhouse.GreenhouseAdapter,
        ApplyMethod.ats_lever: lever.LeverAdapter,
        ApplyMethod.ats_ashby: ashby.AshbyAdapter,
        ApplyMethod.ats_workable: workable.WorkableAdapter,
        ApplyMethod.form: generic_form.GenericFormAdapter,
    }
    adapter = registry.get(method)
    if adapter is None:
        raise ValueError(f"No adapter for apply_method={method}")
    return adapter
