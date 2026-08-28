"""Filter — hard criteria from config.yaml.

Checkpoint 3. Rejects confirmed below-band salary, 2+ years demanded, fees,
unpaid internships, and (when allow_international=false) onsite-outside-India.
Unknown salary is NOT rejected — flagged and ranked lower downstream.
"""

from __future__ import annotations


def passes_filter(job, cfg) -> tuple[bool, str | None]:
    """Return (survives, reason_if_rejected). Built in Checkpoint 3."""
    raise NotImplementedError("filter is built in Checkpoint 3")
