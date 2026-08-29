"""Shared ATS-adapter engine (Playwright).

Both Greenhouse and Lever adapters delegate here. The flow per job:

  1. Build the field map statically (name/email/phone/resume/cover note) — this
     is browser-independent, so the .json artifact is always produced.
  2. Navigate the form (the live posting URL, or a bundled fixture in demo
     mode), fill the known fields, upload resume.pdf.
  3. Pre-submit validation: read every *required* control from the live DOM;
     any required field left empty is an unmapped/unknown question — collect it
     (with its label) and mark the job needs_review rather than guessing.
  4. Screenshot the review-before-submit state to ./outbox/. Do NOT click submit.

The real submit click is reachable only from send() (do_click=True) AND only
when validation is ok — a needs_review job is never submitted. Missing
Playwright / browsers -> blocked_no_browser, never a crash.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from ..config import Config
from ..draft import job_link
from .base import SubmitResult
from .email import OUTBOX, RESUME_PDF, _safe_name

FIXTURES = Path(__file__).resolve().parent / "fixtures"

# JS: report every required control and whether it is filled, with a label.
_REQUIRED_JS = """
() => {
  const ctrls = Array.from(document.querySelectorAll('input, select, textarea'));
  const req = ctrls.filter(el => el.required || el.getAttribute('aria-required') === 'true');
  return req.map(el => {
    let filled;
    if (el.type === 'file') filled = !!(el.files && el.files.length > 0);
    else filled = !!(el.value && String(el.value).trim());
    let label = '';
    if (el.id) { const l = document.querySelector('label[for="' + el.id + '"]'); if (l) label = l.innerText.trim(); }
    if (!label) { const p = el.closest('label'); if (p) label = p.innerText.trim(); }
    if (!label) label = el.name || el.id || el.placeholder || '(unlabeled)';
    return { name: el.name || el.id || '', type: el.type || el.tagName.toLowerCase(), filled, label };
  });
}
"""


def fill_first(page, selector: str, value: str) -> bool:
    """Fill the first element matching `selector` with `value`. Tolerant."""
    if not value:
        return False
    el = page.query_selector(selector)
    if not el:
        return False
    try:
        el.fill(value)
        return True
    except Exception:
        return False


def upload_resume(page, path: str, selector: str = "input[type='file']") -> bool:
    if not path:
        return False
    el = page.query_selector(selector)
    if not el:
        return False
    try:
        el.set_input_files(path)
        return True
    except Exception:
        return False


class AtsAdapterBase:
    """Shared plumbing for Playwright ATS adapters. Subclasses set `ats`,
    `fixture`, `submit_selector`, and implement `fill(page, fields)`."""

    ats = ""
    fixture = ""
    submit_selector = "button[type=submit]"

    def resolve_url(self, job) -> str:
        if job.apply_target and job.apply_target.startswith("http"):
            return job.apply_target
        if job.raw_post and job.raw_post.source_url:
            return job.raw_post.source_url
        return ""

    def fill(self, page, fields: dict) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def dry_run(self, job, application, cfg):
        from ..config import load_resume
        return execute(self, job, application, cfg, load_resume() or {}, do_click=False)

    def send(self, job, application, cfg):
        from ..config import load_resume
        return execute(self, job, application, cfg, load_resume() or {}, do_click=True)


def browser_disabled() -> bool:
    """Force the blocked_no_browser path (for tests / environments w/o a browser)."""
    return os.getenv("JOBHUNTER_DISABLE_BROWSER") == "1"


def demo_fixtures(cfg: Config) -> bool:
    return os.getenv("JOBHUNTER_ATS_FIXTURES") == "1" or cfg.submission.ats_demo_fixtures


def common_fields(job, application, cfg: Config, resume: dict) -> dict:
    name = (resume.get("name") or cfg.signature.name).strip()
    parts = name.split()
    contact = resume.get("contact", {}) or {}
    return {
        "name": name,
        "first_name": parts[0] if parts else name,
        "last_name": " ".join(parts[1:]) if len(parts) > 1 else "",
        "email": cfg.signature.email,
        "phone": contact.get("phone", "") or "",
        "resume_upload": str(RESUME_PDF) if RESUME_PDF.exists() else "",
        "cover_letter": application.body or "",
    }


def _target_url(adapter, job, cfg: Config) -> str:
    if demo_fixtures(cfg):
        return (FIXTURES / adapter.fixture).as_uri()
    return adapter.resolve_url(job)


def _install_hint() -> str:
    return ("Playwright browser not available. Install with: "
            "pip install playwright  &&  playwright install chromium. "
            "Until then, ATS jobs get a field-map .json but no screenshot.")


def _write_record(path: Path, record: dict) -> None:
    OUTBOX.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")


def execute(adapter, job, application, cfg: Config, resume: dict, do_click: bool) -> SubmitResult:
    fields = common_fields(job, application, cfg, resume)
    url = _target_url(adapter, job, cfg)
    base = f"{_safe_name(job)}.{adapter.ats}"
    json_path = OUTBOX / f"{base}.json"
    png_path = OUTBOX / f"{base}.png"

    record = {
        "job_id": job.id,
        "ats": adapter.ats,
        "url": url,
        "recipient_identity": {"from": cfg.signature.email},
        "fields": fields,
        "resume_link": cfg.signature.resume_link,
        "job_link": job_link(job),
    }

    # No browser -> still write the field map, mark blocked, never crash.
    if browser_disabled():
        record["validation"] = "blocked_no_browser"
        record["submitted"] = False
        _write_record(json_path, record)
        return SubmitResult(action="blocked_no_browser", ok=False, outbox_path=str(json_path),
                            note=_install_hint())
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        record["validation"] = "blocked_no_browser"
        record["submitted"] = False
        _write_record(json_path, record)
        return SubmitResult(action="blocked_no_browser", ok=False, outbox_path=str(json_path),
                            note=_install_hint())

    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
            except Exception as exc:  # browser binary missing
                record["validation"] = "blocked_no_browser"
                record["submitted"] = False
                _write_record(json_path, record)
                return SubmitResult(action="blocked_no_browser", ok=False,
                                    outbox_path=str(json_path),
                                    note=f"{_install_hint()} ({type(exc).__name__})")
            page = browser.new_page(viewport={"width": 1000, "height": 1400})
            page.goto(url, wait_until="load", timeout=cfg.llm.request_timeout_seconds * 1000)
            adapter.fill(page, fields)

            required = page.evaluate(_REQUIRED_JS)
            unmapped = [r["label"] for r in required if not r["filled"]]
            validation = "ok" if not unmapped else "needs_review"

            page.screenshot(path=str(png_path), full_page=True)

            clicked = False
            # Gated real submit: only from send(), only when clean, never in demo.
            if do_click and validation == "ok" and not demo_fixtures(cfg):
                page.click(adapter.submit_selector)
                page.wait_for_timeout(800)
                page.screenshot(path=str(OUTBOX / f"{base}.confirm.png"), full_page=True)
                clicked = True
            browser.close()
    except Exception as exc:  # noqa: BLE001 - never crash the batch
        record["validation"] = "error"
        record["error"] = f"{type(exc).__name__}: {exc}"
        record["submitted"] = False
        _write_record(json_path, record)
        return SubmitResult(action="failed", ok=False, outbox_path=str(json_path),
                            error=record["error"])

    record["required_fields_detected"] = required
    record["unmapped_required"] = unmapped
    record["validation"] = validation
    record["screenshot"] = str(png_path)
    record["submitted"] = clicked
    _write_record(json_path, record)

    if clicked:
        action = "submitted"
    elif validation == "ok":
        action = "dry_run"
    else:
        action = "needs_review"
    return SubmitResult(
        action=action,
        ok=(action != "needs_review"),
        outbox_path=str(json_path),
        screenshot=str(png_path),
        detail={"unmapped_required": unmapped, "validation": validation},
        note=None if validation == "ok" else f"needs review: {', '.join(unmapped)}",
    )
