"""Email adapter — Gmail send wired but locked behind an explicit approval gate.

Default action for every drafted job: write a complete, ready-to-send RFC 822
`.eml` into ./outbox/ (subject, To, From/Reply-To, the tuned cover-note body,
resume.pdf attached when present). NOTHING is transmitted.

The real Gmail path (`send_via_gmail`) is only reachable when the CLI passes
`--send --i-confirm` AND the job has a real recipient AND OAuth credentials
exist. Missing/invalid credentials never crash: the send path prints what it
would do and how to authorize, then falls back to writing the .eml.
"""

from __future__ import annotations

import base64
import os
import re
from email.message import EmailMessage
from pathlib import Path

from ..config import PROJECT_ROOT, Config
from ..store import ApplyMethod
from .base import SubmitResult

OUTBOX = PROJECT_ROOT / "outbox"
RESUME_PDF = PROJECT_ROOT / "profile" / "resume.pdf"
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


def recipient_for(job) -> str | None:
    """A real email recipient for the job, or None (URL/DM/unknown targets)."""
    target = (job.apply_target or "").strip()
    if job.apply_method == ApplyMethod.email and _EMAIL_RE.match(target):
        return target
    if _EMAIL_RE.match(target):
        return target
    return None


def _safe_name(job) -> str:
    base = f"{job.id:04d}_{job.company or 'unknown'}_{job.title or 'role'}"
    return re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("_")[:80]


class EmailAdapter:
    method = ApplyMethod.email

    # ------------------------------------------------------------------ #
    # Message construction                                                #
    # ------------------------------------------------------------------ #
    def build_message(self, job, application, cfg: Config) -> EmailMessage:
        msg = EmailMessage()
        msg["Subject"] = application.subject or f"{job.title} — {cfg.signature.name}"
        msg["From"] = cfg.signature.email
        msg["Reply-To"] = cfg.signature.email
        recipient = recipient_for(job)
        if recipient:
            msg["To"] = recipient
        else:
            # No email on the post (ATS/form/DM/unknown). Leave To blank and flag
            # it so a human fills it in before any send.
            msg["To"] = ""
            msg["X-Jobhunter-Needs-Recipient"] = "true"
            msg["X-Jobhunter-Apply-Method"] = job.apply_method.value
            if job.apply_target:
                msg["X-Jobhunter-Apply-Target"] = job.apply_target
        msg["X-Jobhunter-Job-Id"] = str(job.id)
        msg["X-Jobhunter-Fit-Score"] = str(job.fit_score if job.fit_score is not None else "")

        msg.set_content(application.body or "")

        if RESUME_PDF.exists():
            msg.add_attachment(
                RESUME_PDF.read_bytes(),
                maintype="application",
                subtype="pdf",
                filename="Vaishnavi_Resume.pdf",
            )
        else:
            msg["X-Jobhunter-Resume-Note"] = "resume.pdf not found; resume link is in the body"
        return msg

    # ------------------------------------------------------------------ #
    # Dry-run (default) — write .eml, send nothing                        #
    # ------------------------------------------------------------------ #
    def dry_run(self, job, application, cfg: Config) -> SubmitResult:
        OUTBOX.mkdir(parents=True, exist_ok=True)
        msg = self.build_message(job, application, cfg)
        path = OUTBOX / f"{_safe_name(job)}.eml"
        path.write_bytes(bytes(msg))
        recipient = recipient_for(job)
        return SubmitResult(
            action="dry_run",
            ok=True,
            outbox_path=str(path),
            recipient=recipient,
            note=None if recipient else "no recipient on post — To left blank/flagged",
        )

    # ------------------------------------------------------------------ #
    # Real send — GATED. Never called without --send --i-confirm.         #
    # ------------------------------------------------------------------ #
    def send(self, job, application, cfg: Config) -> SubmitResult:
        recipient = recipient_for(job)
        if not recipient:
            # Can't send without a real address; fall back to the dry-run artifact.
            res = self.dry_run(job, application, cfg)
            res.action = "needs_human"
            res.ok = False
            res.note = "no recipient — cannot send; wrote .eml for manual follow-up"
            return res
        msg = self.build_message(job, application, cfg)
        return send_via_gmail(msg, cfg, job, application, self)


# ---------------------------------------------------------------------- #
# Gmail transport — lazy, credential-safe. NEVER crashes for lack of creds. #
# ---------------------------------------------------------------------- #
def _auth_help(cfg: Config) -> str:
    secrets = os.getenv("GMAIL_OAUTH_CLIENT_SECRETS", "./.auth/gmail_client_secret.json")
    return (
        "To enable real sending: (1) create an OAuth client (Desktop) in Google "
        "Cloud with the Gmail API enabled, download the client secret to "
        f"{secrets}; (2) pip install google-api-python-client google-auth-oauthlib; "
        "(3) run `jobhunter send --send --i-confirm` once to complete the consent "
        "flow (scope gmail.send). Until then, everything dry-runs to ./outbox/."
    )


def send_via_gmail(msg: EmailMessage, cfg: Config, job, application, adapter: EmailAdapter) -> SubmitResult:
    """Attempt a real Gmail send. On any missing/broken credential, fall back to
    writing the .eml and return without raising."""
    secrets_path = Path(os.getenv("GMAIL_OAUTH_CLIENT_SECRETS", "./.auth/gmail_client_secret.json"))
    token_path = Path(os.getenv("GMAIL_TOKEN_PATH", "./.auth/gmail_token.json"))

    try:
        from google.auth.transport.requests import Request  # noqa: F401
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError:
        res = adapter.dry_run(job, application, cfg)
        res.action = "needs_auth"
        res.ok = False
        res.note = "google client libraries not installed — wrote .eml instead. " + _auth_help(cfg)
        return res

    scopes = ["https://www.googleapis.com/auth/gmail.send"]
    creds = None
    try:
        if token_path.exists():
            creds = Credentials.from_authorized_user_file(str(token_path), scopes)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            elif secrets_path.exists():
                flow = InstalledAppFlow.from_client_secrets_file(str(secrets_path), scopes)
                creds = flow.run_local_server(port=0)
                token_path.parent.mkdir(parents=True, exist_ok=True)
                token_path.write_text(creds.to_json(), encoding="utf-8")
            else:
                res = adapter.dry_run(job, application, cfg)
                res.action = "needs_auth"
                res.ok = False
                res.note = f"no OAuth credentials at {secrets_path} — wrote .eml instead. " + _auth_help(cfg)
                return res

        service = build("gmail", "v1", credentials=creds)
        raw = base64.urlsafe_b64encode(bytes(msg)).decode()
        sent = service.users().messages().send(userId="me", body={"raw": raw}).execute()
        return SubmitResult(action="sent", ok=True, external_ref=sent.get("id"),
                            recipient=msg["To"])
    except Exception as exc:  # noqa: BLE001 - a broken send must never crash the run
        res = adapter.dry_run(job, application, cfg)
        res.action = "failed"
        res.ok = False
        res.note = f"gmail send failed ({type(exc).__name__}: {exc}) — wrote .eml instead"
        return res
