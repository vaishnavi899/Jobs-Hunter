"""generic_form submission adapter — approval-gated, no auto-send in v1."""

from __future__ import annotations


class GenericFormAdapter:
    """Adapter contract: prepare -> (human approves) -> send.

    Built in its build-order checkpoint. dry_run writes the intended send to
    ./outbox/ and returns without sending. A live send happens only after
    explicit per-application human approval.
    """

    method = "generic_form"

    def submit(self, job, application, cfg, dry_run: bool = True):
        raise NotImplementedError("generic_form adapter is built in its checkpoint")
