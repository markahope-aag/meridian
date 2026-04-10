# Meridian — LLM-Powered Personal Knowledge System

## What It Is

Meridian is a personal knowledge system where an LLM incrementally builds and maintains a wiki from raw source documents. Instead of using RAG (retrieve chunks at query time, forget everything between queries), Meridian has the LLM permanently compile knowledge into interlinked markdown articles that compound over time. The human curates sources and asks questions. The LLM does all the bookkeeping.

Inspired by Andrej Karpathy's concept of an LLM-maintained wiki.

## Three-dimensional knowledge model

A single insight from a client engagement is **cross-filed into three orthogonal dimensions** simultaneously. Each answers a different reader question:

| Dimension | Path | Question | Registry |
|---|---|---|---|
| **Clients** | `wiki/clients/{current,former,prospects}/<slug>/` | "What have we done with X?" | `clients.yaml` |
| **Topics** | `wiki/knowledge/<slug>/` | "What do we know about doing X?" (function) | `topics.yaml` |
| **Industries** | `wiki/industries/<slug>/` | "What do we know about working in X?" (vertical) | `industries.yaml` |

A BluePoint state-pages insight lives in `wiki/clients/current/bluepoint/`, `wiki/knowledge/website/`, and `wiki/industries/financial-services/` with the same evidence behind it. Reading from any dimension surfaces the same underlying knowledge, organized for a different question.

All three registries are **manually curated and compiler-enforced** — no agent can invent a slug. New entries go through a human review queue at `brain.markahope.com/review/taxonomy`.

## How It Works

Documents flow through a multi-stage pipeline that ends in three dimensions of cross-filed knowledge plus per-dimension Layer 3 syntheses:

```
capture/ → distill → raw/ → compiler → wiki/clients/  +  wiki/knowledge/  +  wiki/industries/
                                                  ↓                ↓                  ↓
                                                  └────────────── synthesizer ────────┘
                                                                  ↓
                                                       Layer 3 index.md per slug
```

**Stage 1 — Capture.** Everything lands in `capture/` unfiltered. Sources:
- **Fathom meetings** — Fathom webhook → n8n → receiver formats as markdown
- **Sieve (Google Drive)** — pre-reviewed Drive files post to `/capture/gdrive` (1 MB cap)
- **Web Clipper** — clip any article, Syncthing delivers it
- **Claude Code sessions** — post-session hook captures every coding transcript
- **Manual drops** — `meridian capture --url|--file|--text` from any machine

**Stage 2 — Daily Distill (06:00 UTC).** Always-promote model. Sieve handles human review upstream, so every capture item gets normalized and moved to `raw/`. Distill scoring (relevance / quality) is recorded as metadata but never blocks promotion. Failed scoring marks the file with `distill_status: error` so the next run doesn't re-loop on it.

**Stage 3 — Compile (06:30 UTC).** Two-pass LLM pipeline. Pass 1 (Haiku) plans where to file across all three dimensions, validating every path against `clients.yaml`, `topics.yaml`, and `industries.yaml`. Pass 2 (Sonnet) writes each plan entry in parallel (3 workers). A single client meeting typically produces ~6 cross-filed fragments: 1-2 client docs, 1-2 topic fragments, 1-2 industry fragments.

**Stage 4 — Synthesize (on demand).** Per-dimension Layer 3 synthesis using a two-pass cache architecture: Haiku extracts claims/patterns/contradictions/exceptions to a JSON cache, then Sonnet writes the article from the cache. Prompt iterations re-run only the cheap Sonnet pass. Output is versioned at `state/synthesis_versions/<dim>/<slug>/<timestamp>.md` so prior renders are recoverable without restic.

**Stage 5 — Lint (Sundays 07:00 UTC).** Wiki health check: contradictions, orphans, gaps, suggested connections, client status changes. Dimension-aware (cross-filed copies are not contradictions), registry-validated (won't create stubs at unregistered paths), sanity-capped (won't dump 250 entries into `_index.md` in one run).

**Stage 6 — Watchdog (hourly).** Detects and repairs stuck pipeline state — unfinished extractions, orphaned cache entries, capture files with `distill_status: error`.

## Architecture

All execution happens on a Hetzner VM managed by Coolify. Clients are thin HTTP wrappers. The **dashboard at `brain.markahope.com`** is the primary user surface — Obsidian was retired in April 2026.

```
┌──────────────────────────────────────────────────────────────────┐
│  Hetzner VM (Coolify)                                            │
│                                                                  │
│  ┌────────────────────┐  ┌────────────────────┐  ┌────────────┐ │
│  │ meridian-receiver  │  │ meridian-dashboard │  │  n8n       │ │
│  │ (Flask/Gunicorn)   │  │ (Flask/Gunicorn)   │  │            │ │
│  │  meridian.markahope│  │ brain.markahope.com│  │            │ │
│  │  POST /capture/*   │  │  /                 │  │            │ │
│  │  POST /distill     │  │  /topic/<slug>     │  │            │ │
│  │  POST /compile     │  │  /industry/<slug>  │  │            │ │
│  │  POST /synthesize  │  │  /client/<slug>    │  │            │ │
│  │  POST /lint        │  │  /article/<path>   │  │            │ │
│  │  POST /watchdog    │  │  /search           │  │            │ │
│  │  POST /ask         │  │  /ask              │  │            │ │
│  │  POST /debrief     │  │  /review/taxonomy  │  │            │ │
│  │  POST /context     │  │                    │  │            │ │
│  │  GET  /jobs/<id>   │  │                    │  │            │ │
│  └─────────┬──────────┘  └─────────┬──────────┘  └─────┬──────┘ │
│            │ bind mount            │ bind mount        │        │
│            └───────────┬───────────┘                   │        │
│                        ▼                               │        │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │ /meridian/  (git checkout of april-2026-rebuild)          │ │
│  │   capture/  raw/  wiki/{clients,knowledge,industries}/    │ │
│  │   cache/extractions/{topic,industry}/                     │ │
│  │   state/{jobs.db, synthesis_versions/}                    │ │
│  │   agents/  receiver/  web/  prompts/  scripts/            │ │
│  │   clients.yaml  topics.yaml  industries.yaml              │ │
│  └────────────────────────────────────────────────────────────┘ │
│            ▲                                                     │
│            │ git pull + checkpoint + reload (every minute)       │
│            │                                                     │
│  ┌─────────┴────────────┐    ┌─────────────────────────────┐    │
│  │ vm-auto-deploy.sh    │    │ restic → Cloudflare R2      │    │
│  │ (cron, every minute) │    │ (cron, 03:00 UTC nightly)   │    │
│  └──────────────────────┘    └─────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────┘
```

**meridian-receiver** — Flask/Gunicorn on a Coolify-managed bind mount to `/meridian/`. Central API. Every write to the filesystem and every agent execution goes through it. Endpoints:

| Endpoint | Purpose |
|---|---|
| `POST /capture` | Generic markdown capture (1 MB cap) |
| `POST /capture/fathom` | Fathom meeting webhook (with dedup) |
| `POST /capture/claude-session` | Claude Code session transcript |
| `POST /capture/gdrive` | Google Drive ingestion from Sieve (with dedup, 1 MB cap) |
| `POST /distill` | Run Daily Distill — always-promote (async) |
| `POST /compile` | Run Compiler — cross-files into 3 dimensions (async) |
| `POST /lint` | Run Linter — dimension-aware (async) |
| `POST /synthesize` | Synthesize one topic or industry |
| `POST /synthesize/schedule` | Process pending items in synthesis_queue |
| `GET  /synthesize/queue` | Synthesis queue status |
| `POST /watchdog` | Detect + repair stuck pipeline state |
| `GET  /jobs/<id>` | Poll async job status (SQLite-backed) |
| `POST /ask` | Q&A against the wiki |
| `POST /debrief` | Debrief a Claude Code session |
| `POST /context` | Search wiki, return context brief |
| `GET  /health` | Health check |
| `GET  /check` | Check if a gdrive file already exists |

All endpoints except `/health` require bearer token auth. Pipeline endpoints (`/distill`, `/compile`, `/lint`, `/synthesize`, `/watchdog`) return 202 + job_id by default — add `?sync=true` to block. Job state lives in SQLite at `/meridian/state/jobs.db`, so polls survive worker restarts and route consistently across both gunicorn workers.

**meridian-dashboard** — Flask/Gunicorn on a separate Coolify container. Reads `/meridian/wiki/` directly via bind mount, calls the receiver for write actions. Surfaces all three knowledge dimensions, plus search, Q&A, downloads, and the taxonomy review queue.

**meridian CLI** — pip-installable (`pip install -e ./cli`). Thin wrapper around the receiver API. Same commands work on every machine: `meridian ask`, `debrief`, `context`, `capture`, `lint`, `status`. Reads `~/.meridian/config.yaml` for receiver URL and token.

**n8n** — Event-driven triggers. Six active workflows:
- **Fathom webhook** — real-time, Fathom → receiver
- **Daily Distill** — 06:00 UTC → `POST /distill`
- **Daily Compile** — 06:30 UTC → `POST /compile`
- **Hourly Watchdog** — every hour → `POST /watchdog`
- **Weekly Lint** — Sundays 07:00 UTC → `POST /lint`
- **Daily Synthesize** — currently inactive, run manually

**vm-auto-deploy** — Cron on the VM, every minute. `git ls-remote` against `origin/april-2026-rebuild`, no-op if HEAD matches. On change: checkpoint mutable files (`wiki/log.md`, `_index.md`, `_backlinks.md`, `raw/_index.md`, `clients.yaml`), `git reset --hard`, restore checkpointed files, identify affected containers via `COOLIFY_FQDN`, HUP gunicorn or `docker cp` for hot-patch (dashboard image is baked, not bind-mounted).

**restic + R2** — Daily 03:00 UTC, encrypted incremental snapshot of `/meridian/` to a Cloudflare R2 bucket. Retention: 7 daily / 4 weekly / 12 monthly. Repo password lives in `/root/.meridian-backup.env` and a password manager — losing it means losing the backup.

**Claude Code hooks** — Post-session hook (`~/.claude/hooks/post-session.sh`) fires on Claude Code `Stop`, POSTs the session transcript to `/capture/claude-session`. The debrief agent extracts architectural decisions, patterns that worked, dead ends, and open questions.

## Directory Structure

```
/meridian/
├── AGENTS.md             # source of truth — every agent reads this first
├── README.md             # this file
├── STATUS.md             # current state snapshot
├── capture/              # unfiltered intake (drained by daily distill)
├── raw/                  # promoted source docs with normalized frontmatter
├── wiki/                 # LLM-maintained knowledge base
│   ├── _index.md         # master index (auto-maintained, mutable, gitignored)
│   ├── _backlinks.md     # backlink registry (auto-maintained, mutable, gitignored)
│   ├── log.md            # append-only operations log (mutable, gitignored)
│   ├── concepts/         # free-form concept explainers
│   ├── articles/         # source summaries and analyses
│   ├── clients/          # CLIENT DIMENSION — per-client folders by status
│   │   ├── current/<slug>/
│   │   ├── former/<slug>/
│   │   └── prospects/<slug>/
│   ├── knowledge/        # TOPIC DIMENSION — functional capabilities
│   │   └── <slug>/
│   │       ├── index.md          # Layer 3 synthesis
│   │       ├── client-extractions.md
│   │       └── *.md              # Layer 2 fragments
│   ├── industries/       # INDUSTRY DIMENSION — vertical markets
│   │   └── <slug>/               # same shape as knowledge/<slug>/
│   └── dev/              # Claude Code learnings
├── outputs/              # reports, slides, lint reports
├── cache/                # gitignored, runtime
│   └── extractions/{topic,industry}/<slug>.json
├── state/                # gitignored, runtime
│   ├── jobs.db                       # SQLite job store
│   └── synthesis_versions/<dim>/<slug>/<timestamp>.md
├── agents/               # Python agent scripts
├── prompts/              # LLM system prompts (never hardcoded)
├── receiver/             # Flask API service
├── web/                  # Flask dashboard service
├── cli/                  # pip-installable CLI
├── n8n/                  # importable n8n workflow JSONs
├── scripts/              # setup, deploy, backup, ops scripts
├── tests/synthesis_corpus/   # frozen extraction fixtures + baselines + rubric
├── clients.yaml          # CLIENT REGISTRY — names, slugs, aliases, industry tags
├── topics.yaml           # TOPIC REGISTRY — functional knowledge topics
├── industries.yaml       # INDUSTRY REGISTRY — vertical markets
└── config.yaml           # paths and settings (no secrets)
```

## Key Design Decisions

**AGENTS.md is the source of truth.** Every agent reads it before doing anything. It contains the schema, directory conventions, filing rules, frontmatter spec, and protocol for each agent. Prompts reference it rather than duplicating rules.

**Three-dimensional knowledge model.** Clients × Topics × Industries, all cross-filed simultaneously. Reading from any dimension surfaces the same underlying insight, framed for a different question.

**Registry-enforced filing.** The compiler validates every file path against `clients.yaml`, `topics.yaml`, and `industries.yaml` before writing. It cannot invent new slugs in any dimension — unmatched names are surfaced in the dashboard taxonomy review queue at `/review/taxonomy`. This prevents the proliferation of misspelled/duplicate folders from speech-to-text transcripts and from agent hallucination.

**Always-promote distill.** Sieve handles human review upstream of Meridian, so the daily distill no longer gates content with score thresholds. Every capture item flows through to `raw/`. Distill scoring metadata is still recorded but never blocks promotion.

**Two-pass cached synthesis.** Layer 3 synthesis splits into a Haiku extraction pass (writes to `cache/extractions/<dim>/<slug>.json`) and a Sonnet write pass that reads from cache. Prompt iterations re-run only the cheap write pass — extracting 60+ topics across two dimensions takes seconds instead of hours after the first run.

**Output versioning.** Every synthesis run archives the prior `index.md` to `state/synthesis_versions/<dim>/<slug>/<timestamp>.md` with full provenance stamping (`run_id`, prompt SHAs, model identifiers, cache-hit flag). Rollback is `mv` away — restic only matters for a full disk failure.

**Persistent job store.** Receiver job state lives in SQLite at `/meridian/state/jobs.db`, not in-memory. Polls survive worker restarts and route consistently across both gunicorn workers — fixed the cross-worker "job not found" bug from the in-memory dict era.

**Receiver size caps.** All capture endpoints enforce a 1 MB content limit and return clean JSON 413 errors. Sieve sees the rejection and can surface it to a human, instead of silently wedging the distill queue with corrupt 200 MB Google Docs.

**Git-based deploy.** `/meridian/` is a git checkout of `april-2026-rebuild`. A cron pulls every minute. On change, mutable files are checkpointed before `git reset --hard` and restored after, so runtime state (`wiki/log.md`, `clients.yaml`, etc.) is never clobbered by a deploy. The receiver auto-reloads via SIGHUP; the dashboard hot-patches via `docker cp`.

**Restic + R2 backup.** Nightly encrypted, deduplicated snapshots to a Cloudflare R2 bucket with retention 7d/4w/12m. The repo password lives only in the VM env file and a password manager — losing it means losing the backup, with no recovery path.

**Prompts as files.** All LLM system prompts live in `prompts/*.md`, never hardcoded. Iteration is a `git push` away.

**Append-only operations log.** `wiki/log.md` records every agent action with a consistent format. Gitignored (mutable runtime state), checkpointed across deploys, included in restic snapshots.

**Synthesis regression harness.** `tests/synthesis_corpus/` holds frozen extraction JSON for 6 representative topics plus a known-good baseline. `scripts/test-synthesis.sh` re-runs the write pass over the fixtures; `scripts/diff-synthesis.sh` shows the per-topic diff against a baseline; `scripts/grade-synthesis.py` calls Sonnet to score outputs against a 10-criterion rubric for objective A/B comparison.

## Agents

| Agent | Script | Trigger | Purpose |
|---|---|---|---|
| Daily Distill | `agents/daily_distill.py` | n8n 06:00 UTC | Always-promote `capture/` → `raw/`, normalize frontmatter |
| Compiler | `agents/compiler.py` | n8n 06:30 UTC | Two-pass plan + write, cross-files into 3 dimensions |
| Synthesizer | `agents/synthesizer.py` | Manual / scheduler | Layer 3 synthesis with `--dimension topic\|industry` |
| Synthesis Scheduler | `agents/synthesis_scheduler.py` | Manual | Iterates `synthesis_queue.json`, processes pending items |
| Watchdog | `agents/watchdog.py` | n8n hourly | Detects + repairs stuck pipeline state |
| Linter | `agents/linter.py` | n8n Sunday 07:00 UTC | Dimension-aware wiki health check + sanity-capped auto-fix |
| Debrief | `agents/debrief.py` | POST `/debrief` | Extract learnings from Claude Code sessions |
| Q&A | `agents/qa_agent.py` | POST `/ask` | Research wiki, synthesize answer with citations |

## Getting Started

### New machine setup

```bash
git clone https://github.com/markahope-aag/meridian.git
cd meridian
bash scripts/setup-machine.sh
```

See `scripts/setup-machine.md` for detailed instructions including Syncthing pairing and Obsidian vault setup.

### CLI usage

```bash
meridian status                          # check receiver health
meridian capture --url https://...       # ingest a URL
meridian capture --text "Quick note"     # capture raw text
meridian ask "What is the LLM wiki pattern?"  # query the wiki
meridian context "paid social"           # get a context brief
meridian debrief                         # debrief last Claude Code session
```

## Stack

| Component | Technology |
|---|---|
| Server | Hetzner VM |
| Container orchestration | Coolify |
| Receiver | Python 3.11, Flask, Gunicorn |
| Dashboard | Python 3.11, Flask, Gunicorn (separate Coolify container) |
| LLM | Claude via Anthropic API (Haiku for fast/cheap, Sonnet for quality) |
| Workflow automation | n8n |
| Pre-Meridian review | Sieve (separate project) |
| Meeting capture | Fathom |
| Document ingestion | Google Drive via Sieve |
| Session capture | Claude Code post-session hooks |
| Job state | SQLite |
| Backup | Restic → Cloudflare R2 |
| Deploy | git push → cron auto-pull (every minute) |
| CLI | Python, requests |
| Repo | github.com/markahope-aag/meridian — branch `april-2026-rebuild` |

<!-- deploy test 2026-04-10T13:30:29Z -->
