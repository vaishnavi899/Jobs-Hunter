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

## Install

```bash
uv sync                 # create the venv and install dependencies
uv run jobhunter init   # create runtime dirs + SQLite schema
```

Copy `.env.example` to `.env` and fill in your secrets. Put your resume at
`profile/resume.json` (structured drafting source) and `profile/resume.pdf`
(attached to applications). `profile/` is gitignored — your data never leaves
your machine.

## CLI

```
jobhunter init                 # scaffold dirs, create schema, check config
jobhunter paste "<post text>"  # queue a raw post into the manual inbox
jobhunter ingest               # pull inbox -> raw_posts, dedupe already-seen
jobhunter parse                # raw_posts -> structured jobs (Haiku, or offline heuristic)
jobhunter filter               # apply hard criteria; survivors -> passed_filter
jobhunter score                # 0-100 fit score (weights from config.yaml)
jobhunter draft [-n N]         # tailored drafts for scored jobs (Sonnet, or offline template)
jobhunter send                 # per-channel dry-run to ./outbox/ (email .eml; Greenhouse/Lever field-map .json + screenshot). Sends NOTHING
jobhunter send --demo-fixtures # ATS adapters fill bundled local form fixtures (safe offline demo)
jobhunter send --send --i-confirm   # gated real send/submit (needs a recipient/clean form + creds/browser)
jobhunter run --dry-run        # one full cycle, writes to ./outbox, sends nothing
jobhunter run --live           # one full cycle (still approval-gated per channel)
jobhunter watch                # scheduler loop (poll every N minutes)
jobhunter jobs --status needs_human
jobhunter apply <job_id>       # force a single application
jobhunter stats                # counts by status
jobhunter undo --last          # print exactly what went out last
jobhunter pause / resume       # kill switch (PAUSED file)
```

## Layout

```
config.yaml          # everything tunable: roles, filters, scoring, caps, posture
.env.example         # secret template (copy to .env, never committed)
profile/             # resume.json + resume.pdf  (gitignored — personal data)
inbox/               # drop raw posts here for manual intake  (gitignored)
outbox/              # drafted applications land here for approval  (gitignored)
jobhunter/
  config.py          # config.yaml + .env loader (pydantic-validated)
  store.py           # SQLAlchemy models: raw_posts, jobs, applications, parse_failures
  cli.py             # typer CLI
  sources/           # Source protocol + manual-intake source (x_api drops in later)
  dedupe.py parse.py filter.py score.py draft.py notify.py   # pipeline stages
  submit/            # per-channel adapters + router (email, greenhouse, lever, ...)
```

## Build order

Built as separate commits; each checkpoint pauses for verification.

1. Scaffold, config, SQLite models, CLI skeleton
2. Ingest (manual intake) + dedupe
3. Parse + filter
4. Score + draft
5. Email adapter + dry-run (gated)
6. Greenhouse + Lever ATS adapters (gated)
7. **Ashby, Workable, generic form (gated)** ← current
8. Telegram + scheduler + safety rails

## Safety rails

`--dry-run` first run, a `PAUSED` kill-switch file, per-company 30-day dedupe,
daily caps, randomized inter-submission delay, full send logging, and
`undo --last`. Every send — dry-run or live — is recorded in the
`applications` table.
