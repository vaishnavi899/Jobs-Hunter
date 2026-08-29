"""Telegram notifications + kill-switch commands.

Offline-friendly: with no TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID, every send
no-ops to ./logs/telegram.log and the pipeline runs unchanged — it never
crashes for missing Telegram credentials.

Bot commands (handled by `handle_command`, polled by `watch`):
  /pause  -> writes the PAUSED kill switch; blocks all sending/submitting
  /resume -> clears it
  /status -> paused/running, today's counts, last cycle time
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from ..config import PAUSED_FILE, PROJECT_ROOT, Config, load_config
from ..draft import job_link
from ..store import (
    Application,
    ApplicationStatus,
    Job,
    JobStatus,
    get_session,
)

LOG_PATH = PROJECT_ROOT / "logs" / "telegram.log"
OFFSET_PATH = PROJECT_ROOT / ".auth" / "telegram_offset"
LAST_CYCLE_PATH = PROJECT_ROOT / "logs" / "last_cycle.txt"

_TIER = {1: "Tier 1", 2: "Tier 2", 3: "Tier 3"}


def _salary_str(job: Job) -> str:
    if job.salary_unknown:
        return "unknown"
    v = job.salary_max_inr or job.salary_min_inr
    return f"{v / 100000:g} LPA" if v else "unknown"


def format_new_job(job: Job, state: str = "prepared") -> str:
    """The message the bot sends for a new high-scoring drafted job."""
    return "\n".join([
        f"New match — {job.title or 'role'} @ {job.company or '-'}",
        f"Fit {job.fit_score if job.fit_score is not None else '-'}/100 · "
        f"{_TIER.get(job.role_tier, '-')} · salary {_salary_str(job)}",
        f"Channel: {job.apply_method.value}  ·  {state}",
        job_link(job),
        "Prepared in ./outbox for your approval. Reply /status for today's counts.",
    ])


def _start_of_today() -> datetime:
    return datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


def status_text(cfg: Config | None = None) -> str:
    cfg = cfg or load_config()
    start = _start_of_today()
    with get_session(cfg) as s:
        drafted_today = (
            s.query(Application).filter(Application.created_at >= start).count()
        )
        prepared = (
            s.query(Application)
            .filter((Application.outbox_path.isnot(None)) | (Application.screenshot_path.isnot(None)))
            .count()
        )
        sent_today = (
            s.query(Application)
            .filter(Application.dry_run.is_(False), Application.sent_at >= start)
            .count()
        )
    last_cycle = LAST_CYCLE_PATH.read_text(encoding="utf-8").strip() if LAST_CYCLE_PATH.exists() else "n/a"
    state = "PAUSED (kill switch on)" if cfg.is_paused else "running"
    return "\n".join([
        "jobhunter status",
        f"State: {state}",
        f"Today: drafted {drafted_today}, prepared {prepared} in ./outbox, "
        f"sent {sent_today}/{cfg.caps.applications_per_day}",
        f"Last cycle: {last_cycle}",
    ])


def handle_command(text: str, cfg: Config | None = None) -> str:
    """Handle a /command and return the reply text. Testable offline."""
    cfg = cfg or load_config()
    cmd = (text or "").strip().split()[0].lower() if text.strip() else ""
    if cmd == "/pause":
        PAUSED_FILE.write_text("paused\n", encoding="utf-8")
        return "Paused. Sending/submitting is blocked until /resume."
    if cmd == "/resume":
        if PAUSED_FILE.exists():
            PAUSED_FILE.unlink()
        return "Resumed. Sends still require your explicit approval."
    if cmd == "/status":
        return status_text(cfg)
    return "Commands: /pause, /resume, /status"


class TelegramNotifier:
    def __init__(self, cfg: Config | None = None):
        self.cfg = cfg or load_config()
        self.token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def _log(self, header: str, body: str = "") -> None:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(f"[{ts}] {header}\n")
            if body:
                fh.write(body + "\n")
            fh.write("---\n")

    def send(self, text: str) -> bool:
        if not self.enabled:
            self._log("telegram disabled (no token) — would send:", text)
            return False
        try:
            import httpx

            httpx.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={"chat_id": self.chat_id, "text": text},
                timeout=15,
            )
            self._log("sent:", text)
            return True
        except Exception as exc:  # noqa: BLE001 - never crash on notify
            self._log(f"send failed ({type(exc).__name__}: {exc}) — text:", text)
            return False

    def notify_new_job(self, job: Job, state: str = "prepared") -> str:
        text = format_new_job(job, state)
        self.send(text)
        return text

    def poll_commands(self) -> list[str]:
        """Fetch pending bot commands, handle them, reply. No-op offline."""
        if not self.enabled:
            return []
        replies: list[str] = []
        try:
            import httpx

            offset = None
            if OFFSET_PATH.exists():
                offset = int(OFFSET_PATH.read_text(encoding="utf-8").strip() or "0")
            params = {"timeout": 0}
            if offset is not None:
                params["offset"] = offset + 1
            resp = httpx.get(
                f"https://api.telegram.org/bot{self.token}/getUpdates",
                params=params, timeout=20,
            ).json()
            last_id = offset
            for upd in resp.get("result", []):
                last_id = upd["update_id"]
                msg = upd.get("message") or {}
                txt = msg.get("text", "")
                if txt.startswith("/"):
                    reply = handle_command(txt, self.cfg)
                    replies.append(reply)
                    self.send(reply)
            if last_id is not None:
                OFFSET_PATH.parent.mkdir(parents=True, exist_ok=True)
                OFFSET_PATH.write_text(str(last_id), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            self._log(f"poll_commands failed ({type(exc).__name__}: {exc})")
        return replies
