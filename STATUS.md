# Meridian — Project Status

*Last updated: 2026-04-10*

## The Numbers

| Layer | Count |
|---|---|
| Wiki articles | 3,119 |
| Concepts | 42 |
| Knowledge topics | 86 |
| Industries | 12 (10 synthesized, 2 placeholders) |
| Layer 3 topic syntheses | 62 |
| Layer 3 industry syntheses | 10 |
| Client folders | 47 (40 current, 5 former, 2 prospects) |
| Raw source docs | 780 |
| Capture queue | 0 |
| Git commits | 86 |

## Architecture — three orthogonal knowledge dimensions

A single insight from a client engagement is **cross-filed into all three** dimensions simultaneously. Each dimension answers a different reader question.

| Dimension | Path | Question | Registry |
|---|---|---|---|
| **Clients** | `wiki/clients/{current,former,prospects}/<slug>/` | "What have we done with X?" | `clients.yaml` |
| **Topics** | `wiki/knowledge/<slug>/` | "What do we know about doing X?" (function) | `topics.yaml` |
| **Industries** | `wiki/industries/<slug>/` | "What do we know about working in X?" (vertical) | `industries.yaml` |

All three registries are **manually curated, compiler-enforced** — agents can never invent a slug that isn't in the registry. New entries arrive via the dashboard taxonomy review queue at `/review/taxonomy`.

## Pipeline — fully operational

```
Fathom meetings ──→ n8n webhook ──→ /capture/fathom ──┐
Web Clipper ────────────────────→ Syncthing ─────────┤
Google Drive ───→ Sieve/n8n ────→ /capture/gdrive ───┤
Claude Code ────→ post-session hook ──→ /capture/────┤
Manual ─────────→ meridian capture ──→ /capture ─────┤
                                                      │
                                                      ▼
                                                  capture/
                                                      │
                          Daily Distill (06:00 UTC) ◄─┘
                          Always-promote (Sieve handles human review upstream)
                                      │
                                      ▼
                                    raw/
                                      │
                          Daily Compile (06:30 UTC) ◄─┘
                          Two-pass: Haiku plan → Sonnet write (3 parallel workers)
                          Cross-files into clients/ + knowledge/ + industries/
                                      │
                                      ▼
                          wiki/clients/<slug>/    wiki/knowledge/<topic>/    wiki/industries/<industry>/
                                      │                       │                            │
                                      └───────────────────────┴────────────────────────────┘
                                                              │
                                      Layer 3 Synthesizer (per-dimension, on demand)
                                      Extract cache + write pass + output versioning
                                                              │
                                                              ▼
                                              wiki/<dim>/<slug>/index.md
                                                              │
                                                              ▼
                                              brain.markahope.com (web dashboard)
                                                              │
                                              Weekly Lint (Sun 07:00 UTC)
                                              Dimension-aware, registry-validated stubs, sanity-capped
                                                              │
                                              Hourly Watchdog
                                              Detect + fix stuck pipeline items
                                                              │
                                              Nightly Restic backup → Cloudflare R2 (03:00 UTC)
```

## Clients (40 current, 5 former, 2 prospects)

### Current (40)
AB Hooper, Adava Care, Agility Recovery, AHS (Advanced Health & Safety), American Extractions, Asymmetric, Avant Gardening, AviaryAI, Axley Law, BluepointATM, Blue Sky Capital, Citrus America, The Cordwainer, Crazy Lenny's E-Bikes, Didion, Doudlah Farms, Exterior Renovations, FinWellU, Flynn Audio, HazardOS, Hooper Corp, JBF Concrete, LaMarie Beauty, Machinery Source, Village of Maple Bluff, A New Dawn / Shine, Overhead Door, Paper Tube Co, PEMA, Quarra Stone, Reynolds Transfer, SBS Wisconsin, Seamless Building Solutions, Skaalen, SonoPlot, Three Gaits, Trachte, VCEDC, W.I. Mason's Foundation

### Former (5)
American Extractions, Bake Believe, BluepointATM (legacy), Capitol Bank, Global Coin

## Industries (12 — 10 synthesized, 2 placeholders)

### Synthesized
Healthcare, Senior Living, Nonprofit, SaaS, Food & Beverage, Legal Services, B2B Services, eCommerce & Retail, Financial Services, eLearning

### Placeholders (no clients yet)
Manufacturing, Construction & Home Services

Industries are an orthogonal dimension to topics. The same client (e.g. BluePoint ATM) appears in both `wiki/knowledge/website/` (topic dimension) and `wiki/industries/financial-services/` (industry dimension) for the same insight, by design.

## Knowledge topics (86)

Functional capabilities, organized by category. See `topics.yaml` for the full registry. Highlights:

- **Advertising & Media:** Google Ads, Paid Social, PPC, Programmatic, Amazon Advertising, Retargeting, Influencer Marketing, Event Marketing
- **SEO & Search:** SEO, Local SEO, Technical SEO, AI Search
- **Content & Creative:** Content Marketing, Copywriting, Video Marketing, Design, Branding
- **Email & Automation:** Email Marketing, Marketing Automation, CRM Automation
- **CRM & Sales Tools:** HubSpot, Salesforce, GoHighLevel, CRM
- **Website & Web:** Website, WordPress, Webflow, Web Analytics
- **Ecommerce:** Ecommerce Strategy, Shopify, WooCommerce, Amazon Strategy
- **Analytics & Tracking:** Analytics, Call Tracking, Attribution, Data Quality
- **Integrations & Tech:** Zapier, DNS & Domains, Web Hosting, Integrations
- **AI & Automation:** AI Tools, AI Marketing, AI Agents, AI Workflows
- **Client & Agency Operations:** Agency Operations, Client Management, Project Management, Reporting, Pricing
- **Sales:** Sales Methodology, Sales Enablement, Lead Generation, Outbound Sales, B2B Marketing
- **Strategy:** Marketing Strategy, Brand Strategy, Competitive Analysis, Go-to-Market
- **Compliance & Legal:** Regulatory Compliance, HIPAA Compliance, Legal
- **Team & Operations:** Team Operations, Hiring, Onboarding, Financial Operations

5 topics that used to live here have been migrated to industries: senior-living, nonprofit, saas, elearning, food-beverage. Those are verticals, not functions.

## Automation Layer

| Workflow | Schedule | Status |
|---|---|---|
| Fathom webhook | Real-time | Active |
| Daily Distill | 06:00 UTC | Active |
| Daily Compile | 06:30 UTC | Active (now cross-files into 3 dimensions) |
| Hourly Watchdog | Every hour | Active |
| Weekly Lint | Sunday 07:00 UTC | Active (dimension-aware as of 2026-04-10) |
| VM Auto-deploy | Every minute | Active (`git push` = deploy) |
| Restic Backup | Daily 03:00 UTC | Active (→ Cloudflare R2) |

## Infrastructure

| Component | Status | Notes |
|---|---|---|
| Receiver (`meridian.markahope.com`) | Healthy | Code bind-mounted from `/meridian/receiver/`, gunicorn HUP on push |
| Dashboard (`brain.markahope.com`) | Healthy | Coolify-built Docker image, hot-patched on push |
| n8n (`auto.asymmetric.pro`) | Healthy | 6 active Meridian workflows |
| `/meridian/` git checkout | Healthy | Auto-pulls from `april-2026-rebuild` every minute |
| Restic + Cloudflare R2 | Healthy | Encrypted snapshots, retention 7d/4w/12m |
| SQLite job store | Healthy | `/meridian/state/jobs.db`, cross-worker safe |
| CLI (`meridian` command) | Installed | Same on every machine via setup-machine.sh |
| Claude Code post-session hook | Registered | |
| Fathom webhook | Registered | |
| Google Drive ingestion (Sieve) | Active | 1MB content cap enforced at receiver |

## What's Built

### Receiver (`receiver/app.py`)

Flask + Gunicorn on a Coolify-managed bind mount. Endpoints:

| Endpoint | Purpose |
|---|---|
| `POST /capture` | Generic markdown capture (1 MB cap) |
| `POST /capture/fathom` | Fathom meeting webhook (with dedup by recording_id) |
| `POST /capture/claude-session` | Claude Code session transcript |
| `POST /capture/gdrive` | Google Drive file ingestion (with dedup, 1 MB cap) |
| `POST /distill` | Run Daily Distill (async) |
| `POST /compile` | Run Compiler (async) |
| `POST /lint` | Run Linter (async) |
| `POST /synthesize` | Run synthesizer for one topic/industry |
| `POST /synthesize/schedule` | Process pending synthesis queue items |
| `GET /synthesize/queue` | Synthesis queue status |
| `POST /watchdog` | Detect + repair stuck pipeline items |
| `POST /ask` | Q&A against the wiki |
| `POST /debrief` | Debrief a Claude Code session |
| `POST /context` | Search wiki, return context brief |
| `GET /jobs/<id>` | Poll async job status (SQLite-backed) |
| `GET /health` | Health check |
| `GET /check` | Check if a gdrive file already exists |

Async pipeline endpoints return 202 + job_id by default. Add `?sync=true` to block. Job state lives in SQLite, not in-memory, so polls survive worker restarts and route to either gunicorn worker.

### Dashboard (`web/app.py`)

Flask + Gunicorn on a separate Coolify container. Reads `/meridian/wiki/` directly via bind mount, calls the receiver for write actions. Routes:

| Route | Purpose |
|---|---|
| `/` | Home dashboard — stat grid + clients + topics + industries cards |
| `/topic/<slug>` | Topic page — Layer 3 synthesis + searchable Layer 2 fragment list |
| `/industry/<slug>` | Industry page — same shape as topics, third dimension |
| `/client/<slug>` | Client page — engagement timeline + cross-filed insights |
| `/article/<path>` | Single article view — Markdown render with citation footnotes |
| `/search` | Wiki-wide text search |
| `/ask` | Q&A interface (proxies to receiver) |
| `/review/taxonomy` | Taxonomy review queue — assign industries to unclassified clients |
| `/download/md/<path>` | Download a single article as markdown |
| `/download/pdf/<path>` | Download a single article as PDF |
| `/api/stats` | JSON stats endpoint for external monitors |

### Agents

| Agent | Script | Purpose |
|---|---|---|
| Daily Distill | `agents/daily_distill.py` | Always-promote. Sieve handles human review upstream; Meridian processes everything it receives. |
| Compiler | `agents/compiler.py` | Two-pass plan + write. Cross-files into clients + knowledge + industries. Registry-enforced. |
| Synthesizer | `agents/synthesizer.py` | Layer 3 synthesis with `--dimension topic\|industry`. Extract cache + write pass + output versioning. |
| Synthesis Scheduler | `agents/synthesis_scheduler.py` | Iterates synthesis_queue.json, processes pending items |
| Linter | `agents/linter.py` | Dimension-aware. Registry-validated stub creation. Sanity caps. Cross-filing aware. |
| Watchdog | `agents/watchdog.py` | Hourly. Detects + repairs stuck pipeline state |
| Debrief | `agents/debrief.py` | Extract learnings from Claude Code sessions |
| Q&A | `agents/qa_agent.py` | Research wiki, synthesize answers with citations |

### Registries

| File | Purpose | Entries |
|---|---|---|
| `clients.yaml` | Canonical client names, slugs, aliases, **industry tag** | 58 |
| `topics.yaml` | Canonical knowledge topics, slugs, aliases | 63 |
| `industries.yaml` | **NEW** — canonical industries, slugs, aliases | 12 |

The compiler validates every planned file path against all three registries before writing. Unmatched clients, topics, or industries are flagged via the `/review/taxonomy` queue, never silently created.

### CLI (`meridian` command)

```bash
meridian ask "question"              # Q&A against the wiki
meridian capture --url <url>         # Ingest a URL
meridian capture --file <path>       # Ingest a local file
meridian capture --text "note"       # Capture raw text
meridian context "topic"             # Get a context brief
meridian debrief                     # Debrief last Claude Code session
meridian lint [--dry-run] [--scope]  # Wiki health check
meridian status                      # Check receiver health
```

### n8n Workflows

| File | Schedule | Purpose |
|---|---|---|
| `n8n/fathom-webhook.json` | Real-time | Fathom → receiver |
| `n8n/daily-distill.json` | 06:00 UTC | Drain capture → raw |
| `n8n/daily-compile.json` | 06:30 UTC | Compile raw → wiki (3 dimensions) |
| `n8n/hourly-watchdog.json` | Hourly | Detect + repair stuck items |
| `n8n/lint-weekly.json` | Sunday 07:00 UTC | Wiki health check |
| `n8n/daily-synthesize.json` | (inactive) | Synthesis runs are currently manual |

### Scripts

| Script | Purpose |
|---|---|
| `scripts/setup-machine.sh` | One-command machine onboarding |
| `scripts/setup-machine.md` | Setup instructions for new machines |
| `scripts/install-vm-auto-deploy.sh` | One-time bootstrap for the VM git checkout |
| `scripts/vm-auto-deploy.sh` | Cron body — pull + checkpoint + reload |
| `scripts/backup-restic.sh` | Nightly restic backup to R2 |
| `scripts/restore-restic.sh` | Disaster recovery restore |
| `scripts/backup-restic.md` | Backup runbook |
| `scripts/test-synthesis.sh` | Run synthesis regression harness against frozen fixtures |
| `scripts/diff-synthesis.sh` | Diff a synthesis run against a baseline |
| `scripts/grade-synthesis.py` | LLM-judged evaluation of synthesis quality |
| `scripts/compare-grades.py` | A/B compare two grading runs |
| `scripts/orphan-cleanup-phase1.py` | Reproducible orphan-dir cleanup (one-shot) |
| `scripts/orphan-cleanup-phase3.py` | Reproducible queue-state migration (one-shot) |
| `scripts/industries-migrate.py` | Restore industries content from restic and migrate topics |
| `scripts/classify-clients-by-industry.py` | LLM classifier for client→industry tagging |
| `scripts/hooks/post-session.sh` | Claude Code post-session hook |
| `scripts/ingest-fathom-history.py` | Bulk ingest past Fathom meetings |

## Key Design Decisions

- **AGENTS.md is the source of truth** for all agent behavior
- **Three-dimensional knowledge model**: clients × topics × industries, cross-filed simultaneously
- **Registry-enforced compiler**: `clients.yaml`, `topics.yaml`, `industries.yaml` are mandatory — no agent can invent a slug
- **Two-pass compiler**: Haiku plans (fast), Sonnet writes (quality), 3 parallel workers
- **Two-pass synthesizer with extraction cache**: Pass 1 extracts to cache, Pass 2 writes from cache. Prompt iterations re-run only Pass 2.
- **Synthesis output versioning**: every overwrite copies the prior version to `state/synthesis_versions/<dim>/<slug>/<timestamp>.md` for rollback
- **Always-promote distill**: Sieve handles human review upstream, Meridian ingests everything
- **Receiver size caps**: 1 MB content limit with clean JSON 413 errors so Sieve can surface them
- **Persistent job store**: SQLite at `/meridian/state/jobs.db`, replacing the in-memory dict that caused cross-worker poll bugs
- **All execution on the VM**: CLI and hooks are HTTP clients
- **Prompts as files**: `prompts/*.md`, never hardcoded
- **Capture cleanup**: files deleted after distill (promoted → raw, skipped → deleted)
- **gdrive_file_id provenance**: dedup keys carried through the pipeline so Sieve resubmissions are detected
- **Git-based deploy**: `/meridian/` is a git checkout, `git push` triggers a 1-minute auto-pull on the VM
- **Checkpoint-protected mutable files**: `wiki/log.md`, `wiki/_index.md`, `wiki/_backlinks.md`, `raw/_index.md`, `clients.yaml` survive every `git reset --hard`
- **Restic + R2 backup**: nightly snapshots, encrypted, deduplicated, free egress for restores
- **Synthesis regression harness**: 6 frozen extraction fixtures + LLM-judged 10-criterion rubric for prompt iteration
- **Dashboard taxonomy review queue**: human-in-the-loop registry editing at `/review/taxonomy`
- **Linter is dimension-aware**: cross-filing is not a contradiction, Layer 3 indexes are not orphans, stub creation is registry-validated, index updates are sanity-capped

## Repo

`github.com/markahope-aag/meridian` (public) — currently on branch `april-2026-rebuild`, 86 commits.
