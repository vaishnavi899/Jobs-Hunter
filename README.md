# jobhunter

A self-hosted personal job-application agent. It ingests hiring posts, parses
and filters them against your criteria, scores fit, drafts tailored
applications, and prepares submissions through the right channel — **with a
human approving every send.**

This is a tool you run locally. All secrets (Anthropic key, Gmail OAuth,
Telegram) stay on your machine in `.env`, which is never committed.

## v1 posture (important)

- **Manual intake, not scraping.** No X automation, no paid API. You forward or
  paste raw posts; the pipeline processes them. A paid X API source can be
  added later behind the same `Source` protocol without touching the pipeline.
- **Approval-gated, dry-run by default.** Nothing sends automatically. Every
  draft is written to `./outbox/` and needs explicit human approval before any
  adapter sends. Channels can be graduated to auto-send later, once trusted.

## Setup (run-book)

```bash
uv sync                          # base install — the whole pipeline runs offline with this alone
uv sync --extra groq             # + OpenAI client, for the DEFAULT Groq LLM provider (free tier)
uv sync --extra ats              # + Playwright, for the ATS adapters (Greenhouse/Lever/Ashby/Workable/generic)
uv run playwright install chromium   # one-time browser download (only if you use --extra ats)
uv sync --extra gmail            # + google client libs, only if you want real email sending
uv run jobhunter init            # create runtime dirs + SQLite schema
```

Then:

1. Copy `.env.example` to `.env`. Everything below is **optional** — the tool
   runs fully offline with none of it (heuristic parser, template drafter, `.eml`
   files, no notifications):
   - `GROQ_API_KEY` — the **default** LLM provider (free tier, get one at console.groq.com); needs `uv sync --extra groq`. Enables real parse + draft; else offline heuristics.
   - `ANTHROPIC_API_KEY` — the switchable alternative provider (set `llm.provider: anthropic` in config.yaml to use it).
   - `GMAIL_OAUTH_CLIENT_SECRETS` / `GMAIL_TOKEN_PATH` — for real email sending (Desktop OAuth client, `gmail.send` scope). Put the client-secret JSON under `.auth/`.
   - `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` — notifications + `/pause` `/resume` `/status`. Without them, notifications no-op to `./logs/telegram.log`.
2. Put your resume at `profile/resume.json` (drafting source) and
   `profile/resume.pdf` (attached to applications). `profile/` is gitignored.
3. Tune `config.yaml` — roles, filter thresholds, scoring weights, caps, and the
   dedupe window. Nothing there is code.

## End-to-end command sequence

```
jobhunter paste "<raw hiring post>" --handle "@who"   # or drop files into ./inbox/
jobhunter watch --once            # one full pipeline cycle (ingest→…→draft→send DRY-RUN). Submits nothing.
# or run the stages by hand:
jobhunter ingest && jobhunter parse && jobhunter filter && jobhunter score && jobhunter draft && jobhunter send
jobhunter jobs --status needs_human   # what needs your attention
jobhunter stats                        # counts by status
jobhunter undo --last [-n N]           # revert the last N prepared artifacts
jobhunter watch                        # run the loop forever, every N minutes (config), dry-run only
jobhunter pause / resume               # kill switch (also /pause /resume from Telegram)
```

`jobhunter send` prepares one artifact per drafted job in `./outbox/`, routed by
channel: email → a ready-to-send `.eml`; Greenhouse/Lever/Ashby/Workable → a
field-map `.json` + a review screenshot; any other application URL → the generic
adapter; DM/no-URL → `needs_review`. **It sends nothing.**

## How a real approved send works

A message/application leaves the machine ONLY when **every** gate passes:

1. You pass **`--send --i-confirm`** (both flags; `--send` alone refuses).
2. The artifact **validates** — email: a real recipient; ATS: zero
   `needs_review` fields (unknown required questions block it).
3. **Not paused** — no `PAUSED` file / no Telegram `/pause`.
4. **Under the daily caps** — per-channel, global, and per-company/day.
5. **Not a per-company 30-day duplicate**.
6. Credentials/browser exist (Gmail OAuth for email; Chromium for ATS).

Any single failure → no send, with a clear reason; the dry-run artifact is
written instead. Example (real email, one message, fully gated):

```
jobhunter send --send --i-confirm -n 1
```

## CLI reference

```
jobhunter init | paste | ingest | parse | filter | score | draft   # pipeline stages
jobhunter send [--demo-fixtures] [--send --i-confirm] [-n N]        # prepare/approve (dry-run default)
jobhunter watch [--once]           # scheduler loop (dry-run only), or a single cycle
jobhunter jobs [--status X] | stats
jobhunter undo --last [-n N]       # revert recent prepared artifacts
jobhunter pause | resume           # PAUSED kill switch
```

Telegram bot commands: **`/pause`**, **`/resume`**, **`/status`**.

## Layout

```
config.yaml          # everything tunable: roles, filters, scoring, caps, dedupe, posture
.env.example         # secret template (copy to .env, never committed)
profile/             # resume.json + resume.pdf  (gitignored — personal data)
inbox/  outbox/  logs/  .auth/   (all gitignored)
jobhunter/
  config.py store.py cli.py           # config, SQLAlchemy models, typer CLI
  sources/                            # Source protocol + manual-intake source
  dedupe.py parse.py filter.py score.py draft.py   # pipeline stages
  safety.py                           # send-path rails (pause / caps / 30-day dedupe)
  submit/                             # adapters + router: email, greenhouse, lever, ashby,
                                      #   workable, generic; base.py protocol, _ats.py engine
  notify/telegram.py                  # notifications + /pause /resume /status
  watch.py                            # APScheduler cycle loop (dry-run only)
  undo.py                             # revert recent send records
```

## Build order (all checkpoints complete)

1. Scaffold, config, SQLite models, CLI skeleton
2. Ingest (manual intake) + dedupe
3. Parse + filter
4. Score + draft
5. Email adapter + dry-run (gated)
6. Greenhouse + Lever ATS adapters (gated)
7. Ashby, Workable, generic form (gated)
8. **Telegram + scheduler + safety rails** ← current (final)

## Safety rails

Dry-run by default; the `PAUSED` kill-switch file (`/pause`); per-company
30-day dedupe (at draft *and* send); per-channel + global + per-company daily
caps; randomized inter-submission delay; full logging; and `undo --last`. A
real send additionally requires `--send --i-confirm`, a validated artifact, and
all rails green. Every send — dry-run or live — is recorded in the
`applications` table. `undo` reverts the local record and removes the artifact,
but a **truly-sent** email/submission cannot be un-sent — it says so plainly.
