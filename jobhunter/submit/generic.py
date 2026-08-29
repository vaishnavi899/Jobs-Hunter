"""Generic form adapter (Playwright) — best-effort fallback for any application
form that isn't one of the known ATS platforms.

It heuristically locates name / email / phone / resume-upload / cover-letter
fields by their type and by common label / name / id / placeholder /
autocomplete / aria-label keywords, fills what it can, and then runs the SAME
live-DOM required-field validation as every other adapter. Anything it cannot
confidently map is surfaced as unmapped_required -> needs_review, never guessed.
Approval-gated and dry-run by default, exactly like the ATS adapters.
"""

from __future__ import annotations

from ..store import ApplyMethod
from ._ats import AtsAdapterBase

_LABEL_JS = """
e => {
  let t = '';
  if (e.id) { const l = document.querySelector('label[for="' + e.id + '"]'); if (l) t = l.innerText; }
  if (!t) { const p = e.closest('label'); if (p) t = p.innerText; }
  return t || '';
}
"""

_SKIP_TYPES = {"hidden", "submit", "button", "checkbox", "radio", "image", "reset"}


class GenericAdapter(AtsAdapterBase):
    method = ApplyMethod.form
    ats = "generic"
    fixture = "generic_form.html"
    submit_selector = (
        "button[type=submit], input[type=submit], "
        "button:has-text('Submit'), button:has-text('Apply')"
    )

    def _haystack(self, el) -> str:
        parts = [
            el.get_attribute("name") or "",
            el.get_attribute("id") or "",
            el.get_attribute("autocomplete") or "",
            el.get_attribute("placeholder") or "",
            el.get_attribute("aria-label") or "",
        ]
        try:
            parts.append(el.evaluate(_LABEL_JS) or "")
        except Exception:
            pass
        return " ".join(parts).lower()

    @staticmethod
    def _fill(el, value: str) -> None:
        if not value:
            return
        try:
            el.fill(value)
        except Exception:
            pass

    def fill(self, page, f: dict) -> None:
        controls = page.query_selector_all("input, textarea")
        done: set[str] = set()
        resume_done = False

        for el in controls:
            typ = (el.get_attribute("type") or "").lower()
            try:
                tag = el.evaluate("e => e.tagName.toLowerCase()")
            except Exception:
                tag = "input"
            if typ in _SKIP_TYPES:
                continue

            if typ == "file":
                if not resume_done and f["resume_upload"]:
                    try:
                        el.set_input_files(f["resume_upload"])
                        resume_done = True
                    except Exception:
                        pass
                continue

            hay = self._haystack(el)

            if "email" not in done and (typ == "email" or "email" in hay):
                self._fill(el, f["email"]); done.add("email"); continue
            if "phone" not in done and (typ == "tel" or "phone" in hay or "mobile" in hay):
                self._fill(el, f["phone"]); done.add("phone"); continue
            if "cover" not in done and (
                tag == "textarea" or "cover" in hay or "message" in hay
                or "letter" in hay or "additional" in hay
            ):
                self._fill(el, f["cover_letter"]); done.add("cover"); continue
            if "first" in hay and "name" in hay:
                self._fill(el, f["first_name"] or f["name"]); done.add("name"); continue
            if "last" in hay and "name" in hay:
                self._fill(el, f["last_name"]); continue
            if "name" in hay and "name" not in done and "user" not in hay and "company" not in hay:
                self._fill(el, f["name"]); done.add("name"); continue
