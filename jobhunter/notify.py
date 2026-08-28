"""Notify — Telegram notifications + kill-switch commands.

Checkpoint 8. New-job alerts, submission confirmations, needs-human handoffs,
the 9pm daily digest, and /pause /resume toggling the PAUSED kill switch.
"""

from __future__ import annotations


def notify(message: str, cfg, screenshot: str | None = None) -> None:
    """Send a Telegram message. Built in Checkpoint 8."""
    raise NotImplementedError("notify is built in Checkpoint 8")
