# Meridian — Project Status

*Last updated: 2026-04-06*

## The Numbers

| Layer | Count |
|---|---|
| Wiki articles | 3,898 |
| Concepts | 42 |
| Client folders | 40 current, 5 former |
| Knowledge topics | 67 |
| Raw source docs | 711 |
| Capture (pending) | 80 |
| Git commits | 35 |

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
                    Registry-enforced (clients.yaml + topics.yaml)
                                │
                             wiki/ ──→ Syncthing ──→ Obsidian
                                │
                    Weekly Lint (Sun 7 AM)
                    Auto-fix + flag for review
```

## Clients (40 current, 5 former)

### Current
AB Hooper, Adava Care, Agility Recovery, AHS (Advanced Health & Safety), American Extractions, Asymmetric, Avant Gardening, AviaryAI, Axley Law, BluepointATM, Blue Sky Capital, Citrus America, The Cordwainer, Crazy Lenny's E-Bikes, Didion, Doudlah Farms, Exterior Renovations, FinWellU, Flynn Audio, HazardOS, Hooper Corp, JBF Concrete, LaMarie Beauty, Machinery Source, Village of Maple Bluff, A New Dawn / Shine, Overhead Door, Paper Tube Co, PEMA, Quarra Stone, Reynolds Transfer, SBS Wisconsin, Seamless Building Solutions, Skaalen, SonoPlot, Three Gaits, Trachte, VCEDC, W.I. Mason's Foundation

### Former
American Extractions, Bake Believe, BluepointATM, Capitol Bank, Global Coin

## Knowledge Base (67 Topics)

### Advertising & Media
Google Ads, Paid Social, PPC, Programmatic, Amazon Advertising

### SEO & Search
SEO, Local SEO, Technical SEO, AI Search

### Content & Creative
Content Marketing, Copywriting, Video Marketing, Design, Branding

### Email & Automation
Email Marketing, Marketing Automation, CRM Automation

### CRM & Sales Tools
HubSpot, Salesforce, GoHighLevel, CRM

### Website & Web
Website, WordPress, Webflow, Web Analytics

### Ecommerce
Ecommerce Strategy, Shopify, WooCommerce, Amazon Strategy

### Analytics & Tracking
Analytics, Call Tracking, Attribution

### Integrations & Tech
Zapier, DNS & Domains, Web Hosting, Integrations

### AI & Automation
AI Tools, AI Marketing, AI Agents, AI Workflows

### Client & Agency Operations
Agency Operations, Client Management, Project Management, Reporting, Pricing

### Sales
Sales Methodology, Sales Enablement, Lead Generation, Outbound Sales

### Strategy
Marketing Strategy, Brand Strategy, Competitive Analysis, Go-to-Market

### Industry-Specific
Senior Living, Food & Beverage, Nonprofit, B2B Marketing, eLearning, SaaS

### Compliance & Legal
Regulatory Compliance, HIPAA Compliance, Legal

### Team & Operations
Team Operations, Hiring, Onboarding, Financial Operations

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
| Obsidian vault | Syncing (wiki/ folder only) |
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
| `POST /capture/fathom` | Fathom meeting webhook (with dedup by recording_id) |
| `POST /capture/claude-session` | Claude Code session transcript |
| `POST /capture/gdrive` | Google Drive file ingestion (with dedup) |
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

### Registries

| File | Purpose |
|---|---|
| `clients.yaml` | Canonical client names, slugs, aliases — compiler must match |
| `topics.yaml` | Canonical knowledge topics, slugs, aliases — compiler must match |

The compiler validates every planned file path against these registries before writing. Unmatched clients or topics are flagged, never invented.

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
- **Registry-enforced compiler**: `clients.yaml` and `topics.yaml` are mandatory — the compiler cannot invent new client folders or knowledge topics
- **Two-pass compiler**: Haiku plans (fast), Sonnet writes (quality), 3 parallel workers
- **Cross-filing**: client docs + transferable learnings in `wiki/knowledge/` with backlinks
- **Client status tracking**: current/former/prospect with status transition flagging
- **All execution on VM**: CLI and hooks are just HTTP clients
- **Prompts as files**: `prompts/*.md`, never hardcoded
- **Capture cleanup**: files deleted after distill (promoted → raw, skipped → deleted)
- **Fathom dedup**: by recording_id across capture/ and raw/
- **Obsidian optimization**: only `wiki/` indexed, raw/capture/code excluded via ignore filters

## Repo

`github.com/markahope-aag/meridian` (public) — 35 commits on main
