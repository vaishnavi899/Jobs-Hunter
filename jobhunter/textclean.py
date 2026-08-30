"""Human-readable-text cleanup for the outbound application text.

Any drafter engine (Groq/Anthropic LLM or the offline template) can emit
HTML-escaped entities like `&amp;`, `&#39;`, `&lt;` into what becomes a
text/plain email body — where mail clients do NOT decode entities, so the
recipient would literally see `&amp;`. These helpers decode them.

`unescape_entities` runs html.unescape to a fixpoint, so it also resolves
accidental double-escaping (`&amp;amp;` -> `&`); it is idempotent and a no-op
on already-literal text (a bare `&` is never touched). Kept dependency-light
(stdlib only) so the send/adapter path can import it cheaply.
"""

from __future__ import annotations

import html

# em-dash, en-dash, non-breaking hyphen, figure dash, minus sign -> ASCII "-"
_DASHES = ("—", "–", "‑", "‒", "−")


def unescape_entities(s: str | None) -> str:
    """Decode HTML entities to a fixpoint. Idempotent; no-op on literal text."""
    if not s:
        return s or ""
    cur = s
    for _ in range(5):  # bounded fixpoint (handles single AND double escaping)
        nxt = html.unescape(cur)
        if nxt == cur:
            return cur
        cur = nxt
    return cur


def normalize_subject(s: str | None) -> str:
    """Clean a subject line: decode entities and fold unicode dashes to a plain
    ASCII hyphen so headers read as `... - Vaishnavi` instead of an RFC2047
    `=?utf-8?b?...?=` blob. Whitespace is collapsed/trimmed."""
    out = unescape_entities(s or "")
    for ch in _DASHES:
        out = out.replace(ch, "-")
    return " ".join(out.split())
