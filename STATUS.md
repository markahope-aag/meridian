# Meridian — Project Status

*Last updated: 2026-04-05*

## The Numbers

| Layer | Count |
|---|---|
| Wiki articles | 137 (and growing — compiler is running) |
| Raw source docs | 700 |
| Capture (pending) | 24 |
| Client folders | 19 |
| Knowledge topics | 37 |
| Git commits | 29 |

## Pipeline — Fully Operational

```
Fathom meetings ──→ n8n webhook ──→ /capture/fathom ──→ capture/
Web Clipper ────────────────────→ Syncthing ──────────→ capture/
Google Drive ───→ Sieve/n8n ────→ /capture/gdrive ───→ capture/
Claude Code ────→ post-session hook ──→ /capture/claude-session → capture/
Manual ─────────→ meridian capture ──→ /capture ──────→ capture/
                                                          │
                    Daily Distill (6 AM) ←────────────────┘
                    Score, promote/skip/delete
                                │
                              raw/
                                │
                    Daily Compile (6:30 AM) ←─────────────┘
                    Haiku plans → Sonnet writes (3 parallel)
                                │
                             wiki/ ──→ Syncthing ──→ Obsidian
                                │
                    Weekly Lint (Sun 7 AM)
                    Auto-fix + flag for review
```

## Clients Detected (19)

AB Hooper, Agility Recovery, American Extractions, Asymmetric Applications, Aviary, Cora, Cordwainer, Coristone, Crazy Lindy's, Didion, Doodla, Doudlah Farms, Gus, HazardOS, PaperTube, PIMA, Quarra, Trachte, W.I. Mason's

## Knowledge Base (37 Topics)

Accounting Operations, AI Tools, Amazon Strategy, Articulate Rise 360, Audio Advertising, Business Development, Content Marketing, Content Strategy, CRM Automation, Data Enrichment, Ecommerce Strategy, Email Marketing, Food Regulatory Compliance, Google Ads, Grant Compliance, Instructional Design, Microsoft Clarity, Paid Social, PPC Strategy, Print Design, Product Packaging, Project Management, SaaS Integrations, Salesforce CRM, Sales Methodology, Search Advertising, SEO, SEO Strategy, SharePoint/Salesforce Integration, Team Operations, Trade Show Marketing, Video Optimization, Web Design, Web Forms, Website Strategy, Website Troubleshooting

## Automation Layer

| Workflow | Schedule | Status |
|---|---|---|
| Fathom webhook | Real-time | Active |
| Daily Distill | 6:00 AM | Active |
| Daily Compile | 6:30 AM | Active |
| Weekly Lint | Sunday 7:00 AM | Active |

## Infrastructure

| Component | Status |
|---|---|
| Receiver (`meridian.markahope.com`) | Healthy |
| Syncthing (VM ↔ PC) | Active |
| n8n (`auto.asymmetric.pro`) | 4 Meridian workflows active |
| Obsidian vault | Syncing |
| CLI (`meridian` command) | Installed |
| Claude Code hook | Registered |
| Fathom webhook | Registered |
| Google Drive ingestion | Deployed |

## What's Built

### Receiver (`receiver/app.py`)
Flask/Gunicorn service on Coolify. All endpoints:

| Endpoint | Purpose |
|---|---|
| `POST /capture` | Generic markdown capture |
| `POST /capture/fathom` | Fathom meeting webhook (with dedup) |
| `POST /capture/claude-session` | Claude Code session transcript |
| `POST /capture/gdrive` | Google Drive file ingestion |
| `POST /distill` | Run Daily Distill (async) |
| `POST /compile` | Run Compiler (async) |
| `POST /lint` | Run Linter (async) |
| `POST /ask` | Q&A against the wiki |
| `POST /debrief` | Debrief a Claude Code session |
| `POST /context` | Search wiki, return context brief |
| `GET /jobs/<id>` | Poll async job status |
| `GET /health` | Health check |
| `GET /check` | Check if a gdrive file already exists |

Pipeline endpoints are async by default (return 202 + job_id). Add `?sync=true` for blocking.

### Agents

| Agent | Script | Purpose |
|---|---|---|
| Daily Distill | `agents/daily_distill.py` | Score capture docs, promote to raw, delete processed |
| Compiler | `agents/compiler.py` | Two-pass: Haiku plans, Sonnet writes (3 parallel workers) |
| Debrief | `agents/debrief.py` | Extract learnings from Claude Code sessions |
| Q&A | `agents/qa_agent.py` | Research wiki, synthesize answers with citations |
| Linter | `agents/linter.py` | Wiki health checks, auto-fix + flag for review |

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

| File | Purpose |
|---|---|
| `n8n/fathom-webhook.json` | Fathom → receiver (real-time) |
| `n8n/daily-distill.json` | Distill at 6 AM |
| `n8n/daily-compile.json` | Compile at 6:30 AM |
| `n8n/lint-weekly.json` | Lint on Sundays 7 AM |

### Scripts

| Script | Purpose |
|---|---|
| `scripts/setup-machine.sh` | One-command machine onboarding |
| `scripts/setup-machine.md` | Setup instructions for new machines |
| `scripts/hooks/post-session.sh` | Claude Code post-session hook |
| `scripts/ingest-fathom-history.py` | Bulk ingest past Fathom meetings |

## Key Design Decisions

- **AGENTS.md** is the source of truth for all agent behavior
- **Two-pass compiler**: Haiku plans (fast), Sonnet writes (quality), 3 parallel workers
- **Dynamic client detection**: inferred from content, not a static list
- **Cross-filing**: client docs + transferable learnings in `wiki/knowledge/`
- **Bootstrap → steady state**: permissive thresholds (<20 articles), then autonomous
- **All execution on VM**: CLI and hooks are just HTTP clients
- **Prompts as files**: `prompts/*.md`, never hardcoded
- **Capture cleanup**: files deleted after distill (promoted → raw, skipped → deleted)
- **Fathom dedup**: by recording_id across capture/ and raw/

## Repo

`github.com/markahope-aag/meridian` (public) — 29 commits on main
