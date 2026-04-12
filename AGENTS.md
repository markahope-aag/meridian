# Meridian — Agent Protocol

> Every LLM agent entering this repository MUST read this file first.
> This is the single source of truth for schema, conventions, and filing rules.

## Project Overview

Meridian is an LLM-maintained personal knowledge base organized in three orthogonal dimensions. Documents flow through a pipeline:

```
capture/ → (Daily Distill) → raw/ → (Compiler) → wiki/clients/  +  wiki/knowledge/  +  wiki/industries/
                                                            ↓                   ↓                       ↓
                                                            └──── (Synthesizer per dimension) ──────────┘
                                                                              ↓
                                                                  Layer 3 index.md per slug
```

Humans rarely edit `wiki/` directly. The LLM owns it. The user-facing surface is the dashboard at `brain.markahope.com`. Obsidian was retired in April 2026.

## Three-dimensional knowledge model — READ THIS FIRST

A single insight from a client engagement is **cross-filed into all three dimensions** simultaneously. The compiler does this routing automatically based on the client's industry tag in `clients.yaml`.

| Dimension | Path | Question | Registry |
|---|---|---|---|
| **Clients** | `wiki/clients/{current,former,prospects}/<slug>/` | "What have we done with X?" | `clients.yaml` |
| **Topics** | `wiki/knowledge/<slug>/` | "What do we know about doing X?" (function) | `topics.yaml` |
| **Industries** | `wiki/industries/<slug>/` | "What do we know about working in X?" (vertical) | `industries.yaml` |

**Cross-filing is mandatory, not optional.** A BluePoint state-pages insight produces three files with substantially identical content:

- `wiki/clients/current/bluepoint/2026-04-04-state-pages.md`
- `wiki/knowledge/website/bluepoint-state-pages-strategy.md`
- `wiki/industries/financial-services/bluepoint-state-pages-strategy.md`

This is the design. Don't flag it as a duplicate. Don't suggest merging the files. Each one answers a different reader question and they intentionally drift in framing (client view foregrounds the engagement, topic view foregrounds the technique, industry view foregrounds the vertical context).

**Registry-enforced taxonomy**: agents cannot invent new client, topic, or industry slugs. Unmatched names go to the dashboard taxonomy review queue at `/review/taxonomy`, never to a new folder.

## Architecture

All execution happens on the VM. Clients are thin HTTP wrappers.

```
┌─────────────────────────────────────────────────────────┐
│  Hetzner VM (Coolify)                                   │
│                                                         │
│  ┌─────────────────────┐     ┌───────────────────────┐  │
│  │  meridian-receiver   │     │  n8n                  │  │
│  │  (Flask/Gunicorn)    │◄────│  (event triggers)     │  │
│  │                      │     └───────────────────────┘  │
│  │  POST /capture       │                                │
│  │  POST /capture/fathom│◄─── Fathom webhook             │
│  │  POST /capture/      │                                │
│  │    claude-session    │◄─── Claude Code hook            │
│  │  POST /ask           │                                │
│  │  POST /debrief       │                                │
│  │  POST /context       │                                │
│  └──────────┬───────────┘                                │
│             │ bind mount                                 │
│             ▼                                            │
│  ┌─────────────────────────────────────────────────┐     │
│  │  /meridian/                                     │     │
│  │  capture/ → raw/ → wiki/ → outputs/             │     │
│  └─────────────────────────────────────────────────┘     │
│             │                                            │
└─────────────┼────────────────────────────────────────────┘
              │ Obsidian Sync
              ▼
┌──────────────────────┐
│  Any machine          │
│  - Obsidian (viewer)  │
│  - meridian CLI       │
│    (thin HTTP client) │
│  - Claude Code        │
│    (post-session hook)│
└──────────────────────┘
```

### Components

| Component | Where | Role |
|---|---|---|
| **meridian-receiver** | Coolify container, bind mount to `/meridian/` | Central API — all writes and agent execution |
| **meridian-dashboard** | Coolify container, bind mount to `/meridian/` | Read-side UI at `brain.markahope.com` — three-dim browsing, search, Q&A, taxonomy review |
| **meridian CLI** | Any machine (`pip install -e .`) | Thin HTTP client — `meridian ask`, `debrief`, `context`, `capture`, `lint`, `status` |
| **n8n** | Coolify container | Event-driven triggers (Fathom webhooks, scheduled distill/compile/lint/watchdog) |
| **vm-auto-deploy** | Cron on the VM, every minute | `git pull` + checkpoint + reload — `git push` is the deploy trigger |
| **restic + Cloudflare R2** | Cron on the VM, 03:00 UTC | Encrypted nightly snapshots, retention 7d/4w/12m |
| **Sieve** | Separate project | Pre-Meridian human review for inbound documents (Google Drive, etc.) |
| **Claude Code hooks** | Any machine | Post-session debrief via HTTP to receiver |

### Auth

- Dashboard at `brain.markahope.com` uses session-based login when
  `MERIDIAN_DASHBOARD_PASSWORD` env var is set. CSRF tokens on all
  POST forms. HTML sanitizer on all LLM-generated content.
- All receiver endpoints require `Authorization: Bearer <MERIDIAN_RECEIVER_TOKEN>`
- Token is set as a Coolify env var on the receiver container
- Each local machine stores the token in `~/.meridian/config.yaml`
- n8n includes the token in HTTP Request node headers

### Rate limits and job concurrency

The receiver runs on a 2-worker Gunicorn process. There is no
explicit request rate limiter — the system assumes a single trusted
user (Mark) and scheduled n8n triggers. If the API were ever more
exposed, add these safeguards:

| Concern | Current state | Recommended if public |
|---|---|---|
| Request rate | Unlimited (trusted caller) | Flask-Limiter: 60/min per IP |
| Async job concurrency | Unlimited (threading.Thread per job) | Max 3 concurrent jobs via semaphore |
| LLM API cost | No cap (ANTHROPIC_API_KEY has Anthropic's own limits) | Per-day dollar cap + alert |
| Synthesis queue drain | 5 topics per daily run | Keep — prevents runaway cost |
| Conceptual agent articles | Max 5 per Mode A run | Keep — quality over quantity |
| File writes | No rate limit | Add fsync + max-writes-per-minute for safety |

Async jobs (lint, synthesis, conceptualize) run in daemon threads
inside the Gunicorn worker. A worker restart (SIGHUP from auto-deploy)
kills running background threads — this is the root cause of
"scheduled lint ran but produced no output" failures. The evolution
detector and weekly lint now run via VM cron + `docker exec` to
avoid this problem.

### Local config (`~/.meridian/config.yaml`)

```yaml
receiver_url: https://meridian.markahope.com
token: <MERIDIAN_RECEIVER_TOKEN>
```

## Directory Layout

```
/meridian/
├── AGENTS.md             ← you are here
├── README.md
├── STATUS.md             # current state snapshot
├── capture/              # unfiltered intake — drained by daily distill
├── raw/                  # promoted source docs with normalized frontmatter
├── wiki/                 # LLM-maintained knowledge base — three dimensions
│   ├── _index.md         # master index — ALWAYS read before filing
│   ├── _backlinks.md     # auto-maintained backlink registry
│   ├── log.md            # append-only operations log
│   ├── concepts/         # free-form concept explainers
│   ├── articles/         # source summaries and analyses
│   ├── clients/          # CLIENT DIMENSION
│   │   ├── current/<slug>/
│   │   ├── former/<slug>/
│   │   └── prospects/<slug>/
│   ├── knowledge/        # TOPIC DIMENSION
│   │   └── <slug>/
│   │       ├── index.md          # Layer 3 synthesis (the dimension's anchor)
│   │       ├── client-extractions.md
│   │       └── *.md              # Layer 2 fragments
│   ├── industries/       # INDUSTRY DIMENSION (added 2026-04-10)
│   │   └── <slug>/               # same shape as knowledge/<slug>/
│   └── dev/              # Claude Code learnings
├── outputs/              # reports, lint output, audit artifacts
├── cache/                # gitignored, runtime
│   └── extractions/{topic,industry}/<slug>.json
├── state/                # gitignored, runtime
│   ├── jobs.db                       # SQLite job store
│   └── synthesis_versions/<dim>/<slug>/<timestamp>.md
├── agents/               # Python agent scripts
├── prompts/              # all LLM system prompts as .md files
├── receiver/             # meridian-receiver Flask service
├── web/                  # meridian-dashboard Flask service
├── cli/                  # pip-installable thin client
├── scripts/              # setup, deploy, backup, ops
├── tests/synthesis_corpus/   # frozen extraction fixtures + baselines + rubric
├── n8n/                  # importable workflow JSONs
├── clients.yaml          # CLIENT REGISTRY — name, slug, aliases, industry tag
├── topics.yaml           # TOPIC REGISTRY
├── industries.yaml       # INDUSTRY REGISTRY (added 2026-04-10)
└── config.yaml           # paths and settings (no secrets)
```

## Receiver API

All endpoints except `/health` require `Authorization: Bearer <token>`. All capture endpoints enforce a 1 MB content limit and return JSON 413 envelopes when exceeded so upstream callers (Sieve, Fathom, etc.) can surface the rejection.

### Capture endpoints

| Endpoint | Method | Purpose |
|---|---|---|
| `POST /capture` | Generic | Write any `.md` payload to `capture/` (1 MB cap) |
| `POST /capture/fathom` | Fathom-specific | Format `new-meeting-content-ready` webhook payload (with dedup by `recording_id`) |
| `POST /capture/claude-session` | Claude Code | Convert JSONL session transcript to `.md` in `capture/` |
| `POST /capture/gdrive` | Sieve | Ingest a Google Drive file by ID (with dedup by `gdrive_file_id`) |
| `GET  /check` | Sieve | Check if a `gdrive_file_id` already exists in `capture/`, `raw/`, or `wiki/` |

### Pipeline endpoints (async)

Return `202 Accepted` with a `job_id` by default. Poll `GET /jobs/<id>` for results. Add `?sync=true` for synchronous execution. Job state is SQLite-backed at `/meridian/state/jobs.db` and survives gunicorn worker restarts.

| Endpoint | Purpose |
|---|---|
| `POST /distill` | Run Daily Distill — always-promote `capture/` → `raw/` (Sieve handles human review upstream) |
| `POST /compile` | Run Compiler — cross-files raw docs into clients/, knowledge/, and industries/ |
| `POST /synthesize` | Run synthesizer for one slug. Body: `{"topic": "<slug>", "dimension": "topic|industry"}` |
| `POST /synthesize/schedule` | Process the synthesis queue. Body: `{"limit": 5}` |
| `GET  /synthesize/queue` | Synthesis queue status (no auth required) |
| `POST /lint` | Run Linter — dimension-aware wiki health check + sanity-capped auto-fix |
| `POST /watchdog` | Detect + repair stuck pipeline state |
| `GET  /jobs/<id>` | Poll job status: `running`, `completed`, or `failed` |

### Agent endpoints

| Endpoint | Purpose |
|---|---|
| `POST /ask` | Accept a question, run Q&A agent against wiki, return result, file to `outputs/` |
| `POST /debrief` | Accept a session transcript, run debrief agent, file to `capture/` |
| `POST /context` | Accept a topic, search wiki, return a context brief |

### CLI commands

| Command | Calls | Description |
|---|---|---|
| `meridian ask "question"` | `POST /ask` | Ask the knowledge base a question |
| `meridian debrief` | `POST /debrief` | Debrief a Claude Code session |
| `meridian context "topic"` | `POST /context` | Get a context brief on a topic |
| `meridian capture --url <url>` | `POST /capture` | Ingest a URL into capture |
| `meridian capture --file <path>` | `POST /capture` | Ingest a local file into capture |
| `meridian capture --text "note"` | `POST /capture` | Capture raw text |
| `meridian lint` | `POST /lint` | Wiki health check (async, polls until complete) |
| `meridian lint --dry-run` | `POST /lint` | Report only, no changes |
| `meridian lint --scope orphans` | `POST /lint` | Run specific check only |
| `meridian status` | `GET /health` | Check receiver health |

## Frontmatter Schema

### Raw documents (`raw/`)

Every file in `raw/` MUST have this frontmatter:

```yaml
---
title: "Document Title"
source_url: "https://..."
source_type: article | paper | repo | dataset | image | note | meeting | claude-session
date_ingested: "2026-04-04"
compiled_at:                # blank until compiler processes it
tags: []
summary: ""                 # one line, filled by compiler
---
```

### Wiki articles (`wiki/`)

```yaml
---
title: "Article Title"
type: concept | article | category | index
created: "2026-04-04"
updated: "2026-04-04"
source_docs: []             # list of raw/ filenames this was compiled from
tags: []
---
```

### Layer 2 articles (`wiki/knowledge/`, `wiki/clients/`)

Layer 2 articles are compiled from raw sources — client work, meeting transcripts,
ingested documents. They add these fields to the base wiki frontmatter:

```yaml
---
layer: 2
client_source: "Doudlah Farms"   # client name if from client work, null if not
industry_context: "food-beverage" # industry slug if applicable
transferable: true                # true if insight applies beyond this client
---
```

### Layer 3 articles (`wiki/knowledge/`)

Layer 3 articles are synthesized knowledge — distilled from multiple Layer 2 sources
into authoritative topic articles. They track confidence, currency, and domain dynamics:

```yaml
---
layer: 3
source_count: 5                        # number of Layer 2 sources synthesized
current_status: current                # current | evolving | superseded | deprecated
rate_of_change: moderate               # stable | slow | moderate | high | volatile
domain_type: platform-tactics          # fundamental | strategy | platform-tactics |
                                       #   platform-mechanics | regulatory
confidence: high                       # low | medium | high | established
evidence_count: 8                      # total evidence citations
first_seen: "2025-10-15"              # earliest source date
last_updated: "2026-04-06"
supporting_sources: []                 # source docs that support this knowledge
contradicting_sources: []              # source docs that contradict
contradicting_count: 0                 # count of contradicting_sources (auto-computed)
hypothesis: false                      # true if unvalidated theory
evolution_timeline: []                 # list of {date, event, note} — auto-appended by evolution_detector
evolution_start: null                  # date first detected that knowledge is changing
superseded_by: null                    # link to replacement article if deprecated
superseded_date: null                  # date the article was superseded
deprecation_notice: null               # banner text shown at top of article if superseded
web_monitoring_frequency: monthly      # none | quarterly | monthly | weekly | continuous
---
```

**Evolution tracking fields** (`evolution_timeline`, `evolution_start`,
`current_status`, `contradicting_count`, `superseded_by`,
`superseded_date`, `deprecation_notice`) are set or updated
automatically by `agents/evolution_detector.py` on its weekly run
(Sundays 08:00 UTC). They track when a Layer 3 article's knowledge
is changing and flag it for re-synthesis or supersession. The
synthesizer's write prompt sets the default values at synthesis
time; the detector mutates them over time as new evidence arrives.

### Layer 4 articles (`wiki/layer4/`)

Layer 4 is the **conceptual layer**. Articles in `wiki/layer4/` are
written by `agents/conceptual_agent.py`, not by the compiler or
synthesizer. They contain insight that only emerges from reading
across multiple Layer 3 articles simultaneously — patterns, emerging
trends, resolved contradictions, and drift flags.

```
wiki/layer4/
├── _index.md                 # auto-maintained by conceptual agent
├── patterns/                 # non-obvious cross-topic connections
├── emergence/                # proto-patterns (not yet established)
├── contradictions/           # resolved cross-topic contradictions
└── drift/                    # created by Phase 6 evolution detector
```

```yaml
---
title: "Landing Page Quality as a Google Ads Forcing Function"
layer: 4
concept_type: pattern                  # pattern | emergence | contradiction | drift
topics_connected:                      # Layer 3 article paths this concept binds
  - wiki/knowledge/google-ads/index.md
  - wiki/knowledge/website/index.md
industries_connected: []               # Layer 3 industry paths, if applicable
confidence: low                        # low | medium | high | established
first_detected: "2026-04-12"           # when the conceptual agent first noticed it
last_updated: "2026-04-12"
hypothesis: true                       # true until evidence reaches medium+ confidence
supporting_evidence_count: 2           # updated by Mode B (pattern maturation)
contradicting_evidence_count: 0
status: active                         # active | resolved | superseded
---
```

**Rules for linter, compiler, and other agents:**

- **Never flag Layer 4 articles as orphans.** They're not reachable
  via wikilinks from Layer 2 fragments by design — nothing in the
  raw corpus knows they exist until the conceptual agent writes them.
- **Never apply Layer 2/3 quality rules to Layer 4.** No Layer 4
  article has `client_source`, `source_docs`, or the Layer 3
  confidence-gradation vocabulary. Its evidence lives in the
  `topics_connected` links.
- **Never suggest a Layer 4 connection yourself.** The conceptual
  agent makes that judgment across the full L3 map. The linter can
  report stats but must not write speculative pattern stubs.
- **Registry enforcement still applies.** A Layer 4 article's
  `topics_connected` and `industries_connected` lists must reference
  existing canonical slugs in `topics.yaml` and `industries.yaml`.
  No inventing new topic slugs in a Layer 4 article.
- **The linter should report Layer 4 article count** in its summary
  section but otherwise leave Layer 4 files untouched — no
  backlinks rebuilding, no index-entry auto-adding.
- **Engineering and interests namespaces are excluded from Layer 4
  analysis** in Phase 7. The conceptual agent reads only
  `wiki/knowledge/` and `wiki/industries/` Layer 3 articles. This
  may expand in a later phase once engineering has enough L3
  articles to produce cross-topic patterns.

**Quality standard** (the hard bar): a Layer 4 article belongs in
Layer 4 if and only if it contains insight that could not have been
written without reading multiple Layer 3 articles simultaneously. A
summary of a single Layer 3 article is not Layer 4 — it belongs in
that article's own `## Current Understanding` section. A connection
between two Layer 3 articles that adds something neither contains
alone — that is Layer 4.

### Capture documents (`capture/`)

Capture docs have minimal or no frontmatter — they arrive in whatever format the source
provides. The Daily Distill agent normalizes them when promoting to `raw/`.

## Filing Rules

1. **Always read `wiki/_index.md` first.** Before creating or modifying any wiki article,
   read the full index to understand the current structure and avoid duplicates.

2. **One concept per file.** Don't create monolithic pages. If a topic has distinct
   subtopics, each gets its own file with cross-links.

3. **File names are kebab-case.** Examples: `bluepoint-state-pages-strategy.md`,
   `2026-04-04-team-standup.md`.

4. **Backlinks are mandatory.** When article A references article B, both files must
   reflect the link. The linter rebuilds `_backlinks.md` from actual link state.

5. **Categories are emergent.** Don't pre-create categories. When 3+ articles share a
   theme, create a category page that links to them.

6. **Incremental updates only.** Never rewrite an existing article from scratch unless
   explicitly asked. Append, amend, or create a new related article instead.

7. **Source attribution.** Every wiki article must list its source documents in the
   `source_docs` frontmatter field.

8. **Append to `wiki/log.md` after every operation.** Every agent must log what it did.

9. **Registry-enforced taxonomy — non-negotiable.** The compiler validates every file
   path against three registries before writing:

    - **Client paths** — `wiki/clients/<status>/<slug>/...` — `<slug>` MUST exist in `clients.yaml`
    - **Topic paths** — `wiki/knowledge/<slug>/...` — `<slug>` MUST exist in `topics.yaml`
    - **Industry paths** — `wiki/industries/<slug>/...` — `<slug>` MUST exist in `industries.yaml`

    Aliases listed under each registry entry let the compiler match variant phrasings.
    On detecting an unmatched name, the compiler emits an `unmatched_*` flag in its plan
    output and the dashboard surfaces it in the `/review/taxonomy` queue. Never invent
    a new slug. The cleanup of 37 orphan directories on 2026-04-10 was the cost of one
    earlier era of unenforced taxonomy.

10. **Cross-filing into all three dimensions.** Every client-tied insight produces three
    plan entries with the same body and identical filenames across the topic and
    industry dimensions:

    ```
    wiki/clients/current/<client-slug>/<date>-<headline>.md
    wiki/knowledge/<topic-slug>/<client-slug>-<headline>.md
    wiki/industries/<industry-slug>/<client-slug>-<headline>.md
    ```

    The industry slug comes from the client's `industry` field in `clients.yaml`. If the
    client has no industry tag, skip the industry entry — never guess. Internal
    meetings (ops-sync, weekly-call, sprint-planning) are not client-tied and get filed
    only to `wiki/articles/`, no cross-files.

11. **Client status.** The compiler infers status from context:

    - **Current** — active campaigns, recent meetings, ongoing work
    - **Former** — past tense, "when we worked with", closed projects, no recent activity
    - **Prospect** — proposal language, discovery calls, RFP references

    File under `wiki/clients/{current,former,prospects}/<slug>/`. Use the canonical
    slug from `clients.yaml`. Never invent.

12. **Client status transitions.** If the compiler sees signals that a client's status has
    changed (e.g. "we've wrapped up with X"), it flags the transition for review rather
    than moving the folder automatically:
    `"status_change": {"client": "acme", "from": "current", "to": "former", "signal": "..."}`
    The linter independently checks `last_activity` dates against engagement signals
    and surfaces stale `current` clients for `current → former` reclassification.

13. **Client index.** Each `wiki/clients/<status>/<slug>/_index.md` is maintained by the
    compiler and must track:

    - Status (current / former / prospect)
    - **Industry** (the slug from `industries.yaml` — used by the compiler for cross-filing)
    - First seen date
    - Last activity date
    - Key contacts (extracted from meeting attendees)
    - Active projects
    - Links to all related docs

14. **Knowledge index.** `wiki/knowledge/<slug>/index.md` is the Layer 3 synthesis for
    a topic, written by the synthesizer. Don't write to it from the compiler — the
    compiler only writes Layer 2 fragments. The synthesizer aggregates them.

15. **Industry index.** `wiki/industries/<slug>/index.md` is the Layer 3 synthesis for an
    industry, written by the synthesizer with `--dimension industry`. Same rule:
    compiler writes fragments, synthesizer writes the index.

## Operations Log (`wiki/log.md`)

Append-only log of all agent activity. Every agent appends an entry after completing its
operation. Never edit or delete existing entries.

### Format

```markdown
## [YYYY-MM-DD] operation | description

Details of what was done.
```

### Operations

| Operation | Agent | Description |
|---|---|---|
| `ingest` | receiver | Document captured to `capture/` |
| `distill` | daily_distill | Document scored and promoted to `raw/` |
| `compile` | compiler | Raw document compiled into wiki article(s) across 3 dimensions |
| `synthesize` | synthesizer | Layer 3 article generated for one topic or industry |
| `watchdog` | watchdog | Stuck pipeline state detected and repaired |
| `lint` | linter | Wiki health check completed |
| `query` | qa_agent | Question answered against the wiki |
| `extract` | scripts/extract-client-learnings | Layer 2 extraction (one-shot, historical) |

### Example entry

```markdown
## [2026-04-04] distill | Promoted "Team Standup 2026-04-04" to raw/

Relevance: 8, Quality: 7. Contains architecture decision on auth middleware.
Filed as raw/2026-04-04-team-standup.md with tags: [meeting, auth, architecture].
```

## Agent Conventions

### Environment
- `ANTHROPIC_API_KEY` is available as an environment variable on the receiver container
- All agent scripts run on the VM, invoked by the receiver or n8n
- Python scripts use stdlib + `anthropic` + `requests` — minimal dependencies
- All prompts live in `prompts/` as `.md` files — never hardcode prompts in scripts

### CLI Interface
Every tool and agent script must:
- Accept arguments via CLI (argparse or sys.argv)
- Print structured output to stdout (JSON preferred for tools, markdown for agents)
- Exit with code 0 on success, non-zero on failure
- Log to stderr, never stdout (stdout is for output)

### Error Handling
- If `wiki/_index.md` doesn't exist, create it — don't crash
- If a referenced file is missing, log a warning and continue
- Never silently swallow errors — log them to stderr

## Compilation Protocol

The compiler uses a two-pass architecture for speed:

**Pass 1 — Planning (Haiku, fast):** Reads the raw document and `wiki/_index.md` plus
all three registries (`clients.yaml`, `topics.yaml`, `industries.yaml`) and a compact
client→industry lookup. Decides what files to create/update across all three dimensions.
Detects clients, infers status, identifies transferable learnings. Returns a filing plan
as JSON. Every path is then validated against the registries — unmatched slugs get an
alias-match attempt, then a `validation_warnings` entry if still no match.

**Pass 2 — Writing (Sonnet, parallel):** Takes the validated plan and writes each wiki
file concurrently (3 workers). Each worker gets the raw document and one plan entry.

**After all files:** Index and backlinks are updated once in a batch. Append to
`wiki/log.md`.

Multiple documents compile in parallel (3 concurrent). Target: 5 documents < 60 seconds.

### Cross-filing into the three dimensions

For every client-tied insight, the planner emits **three plan entries** with the same
filename across the topic and industry dimensions:

```json
{
  "plan": [
    {"path": "wiki/clients/current/bluepoint/2026-04-04-state-pages.md", "title": "..."},
    {"path": "wiki/knowledge/website/bluepoint-state-pages-strategy.md", "title": "..."},
    {"path": "wiki/industries/financial-services/bluepoint-state-pages-strategy.md", "title": "..."}
  ]
}
```

The third entry's industry slug is looked up from the client's `industry` field in
`clients.yaml`. If the client has no industry tag, the third entry is skipped — never
guess. Internal meetings (ops-sync, weekly-call) are not client-tied and produce only
a `wiki/articles/` entry, no cross-files.

### Source type routing

| Source type | Destination | Notes |
|---|---|---|
| article, paper, note | `wiki/articles/` or `wiki/concepts/` | Standard routing |
| meeting | `wiki/articles/` + cross-files if client-tied | Extract decisions + action items as cross-links |
| claude-session | `wiki/dev/` | Route by content type (see below) |
| any (client-tied) | All three dimensions | See cross-filing rules above |

### Claude Code session routing (`claude-session`)

| Content | Destination | Filename pattern |
|---|---|---|
| Architectural decisions | `wiki/dev/decisions/` | `decision-{slug}.md` |
| Reusable patterns | `wiki/dev/patterns/` | `pattern-{slug}.md` |
| Failed approaches | `wiki/dev/dead-ends/` | `dead-end-{slug}.md` |

A single debrief may produce multiple files.

## Daily Distill Protocol

**Always-promote** as of 2026-04-09. Sieve handles human review upstream of Meridian
(see https://github.com/.../sieve), so every capture item flows to `raw/`. Distill
scoring is recorded as metadata for downstream signals but never blocks promotion.

Steps for each unprocessed file in `capture/`:

1. Read the file content
2. Call the LLM with the distill scoring prompt (Sonnet, temperature 0.3)
3. On success: stamp `distill_status: promote` + scores in the frontmatter, then
   call `promote_to_raw` which carries provenance keys (`gdrive_file_id`,
   `recording_id`, `session_id`, etc.) into the normalized raw frontmatter
4. On scoring failure: stamp `distill_status: error` with the error message,
   STILL promote to raw (Sieve already vetted it; Meridian's job is to ingest
   what it receives). The error stamp prevents the next run from re-looping.
5. Delete the source file from `capture/` after the raw copy lands

The receiver enforces a 1 MB content cap on `/capture/*` so corrupt or
pathologically large files are rejected at the boundary, not at distill time.

## Synthesizer Protocol

Two-pass cached architecture, dimension-aware. Triggered manually or by the
synthesis scheduler.

**CLI shape:**

```
synthesizer.py extract --topic <slug> [--dimension topic|industry] [--re-extract]
synthesizer.py write   --topic <slug> [--dimension topic|industry]
synthesizer.py run     --topic <slug> [--dimension topic|industry] [--force] [--re-extract]
```

`synthesize_topic(slug, dimension)` is the public function the scheduler imports.

**Pass 1 — Extraction (Haiku):** Reads all Layer 2 fragments under
`wiki/{knowledge|industries}/<slug>/` (skipping `index.md`, `_index.md`,
`PLACEHOLDER.md`). Batches them into groups of 20 and asks Haiku to extract
claims, patterns, contradictions, exceptions, evidence, and client mentions.
Merges the per-batch output and writes a JSON cache file at
`cache/extractions/<dimension>/<slug>.json` with the schema version,
extraction prompt SHA, fragment count, and newest fragment mtime.

**Cache invalidation:** Pass 1 reuses an existing cache if and only if (a) the
schema version matches, (b) the extract prompt SHA matches, and (c) every
fragment's mtime is older than the cache file. Any failure invalidates and
re-extracts.

**Pass 2 — Writing (Sonnet):** Reads the cache, calls Sonnet with the write
prompt + extracted data + topic metadata, gets back a complete markdown article
(frontmatter + body). Stamps Meridian provenance fields into the frontmatter
(`generated_at`, `run_id`, `synthesizer_prompt_sha`, `extract_prompt_sha`,
`writer_model`, `extract_model`, `extraction_cache_hit`).

**Output versioning:** Before overwriting the existing `index.md`, copy it to
`state/synthesis_versions/<dimension>/<slug>/<timestamp>.md`. Rollback is
`mv` away — restic only matters for full disk failures.

**Test harness flags:** `--fixture <path>` and `--output <path>` let the
regression harness in `tests/synthesis_corpus/` run the write pass against
frozen extraction inputs without touching production state.

## Linter Protocol

Weekly wiki health check (Sundays 07:00 UTC via n8n). Dimension-aware as of
2026-04-10. Reads a **proportional sample** of wiki content across all
dimensions (not alphabetical-first within a 150K cap), pulls all three
registries for stub validation, and asks the LLM for four reports.

### Three-dimensional awareness

The linter must NOT flag:
- **Identical content across the three dimensions** as a contradiction. Each
  insight is intentionally cross-filed.
- **Cross-dimension copies** as orphans. A `wiki/industries/` file may have
  no inbound wikilinks but still be valid because it's a mirror.
- **Cross-dimension copies** as suggested connections.
- **`index.md`, `_index.md`, `_backlinks.md`, or `PLACEHOLDER.md`** as orphans.
  Layer 3 indexes are anchor pages reached by browsing, not by wikilink.

### Auto-fix (linter acts directly, with safety caps)

- **Rebuild `_backlinks.md`** from actual wikilink state. Always safe.
- **Add missing `_index.md` entries** — but only for `wiki/concepts/`,
  `wiki/articles/`, and Layer 3 anchor `index.md` files in the topic /
  industry dimensions. Layer 2 fragments under `wiki/clients/`,
  `wiki/knowledge/`, and `wiki/industries/` are intentionally NOT in the
  global index — they're discoverable via their parent dimension page.
  **Sanity cap: 50 additions per run.** Anything over goes to the deferred
  list for human review.
- **Create stub articles** for concepts mentioned in 5+ articles. Stub
  location MUST validate against the registries:
  - `wiki/concepts/*` and `wiki/articles/*` are free-form, allowed
  - `wiki/clients/<status>/<slug>/*` requires `<slug>` in `clients.yaml`
  - `wiki/knowledge/<slug>/*` requires `<slug>` in `topics.yaml`
  - `wiki/industries/<slug>/*` requires `<slug>` in `industries.yaml`
  - Anything else is rejected and held for review
  **Sanity cap: 20 stubs per run.**

### Flag for review (no auto-fix)

- Contradictions between distinct insights (across dimensions OK if content differs)
- Client status changes (current → former signals)
- Orphans with no clear home in any dimension
- Sub-5-mention gaps (article candidates)
- Suggested connections between unlinked articles
- Anything held due to a sanity cap or registry validation failure

### Output

- Full report: `outputs/lint-<date>.md`
- Wiki copy: `wiki/articles/lint-<date>.md`
- New "Held For Review" section enumerating deferred items
- Log entry appended to `wiki/log.md`

The linter owns structural fixes (links, index, stubs). The compiler owns content.
The linter never modifies wiki article content directly.

## Watchdog Protocol

Hourly via n8n. Detects pipeline state that the normal flow has left stuck:

- **`capture/` files with `distill_status: error`** older than 24 hours — log a warning so they show up in the dashboard
- **`raw/` files with empty `compiled_at`** older than 3 days — re-trigger compile for them
- **`synthesis_queue.json` items in `running` state** older than 2 hours — flip to `pending` (a previous run was killed mid-synthesis)
- **Orphan extraction caches** referencing fragments that no longer exist — log + mark for cleanup

The watchdog never deletes content. It only re-triggers, resets state, or logs.

## Layer 4 Conceptual Protocol

The conceptual agent (`agents/conceptual_agent.py`) runs four modes on
a schedule. Unlike every other pipeline stage, Layer 4 is not triggered
by new documents — it runs continuously across the *existing* knowledge
base looking for what cannot be seen from any single Layer 3 article.

All four modes share a cached in-memory map of every Layer 3 article
in `wiki/knowledge/` and `wiki/industries/` (topic slug → summary +
key claims + client mentions; industry slug → summary + key claims;
plus cross-references). The cache lives at `cache/layer4/l3_map.json`
and is invalidated when any index.md mtime is newer than the cache
or the schema version drifts.

### Mode A — Connection Discovery  (Sunday 09:00 UTC)

Reads the L3 map, finds non-obvious cross-topic connections, writes
at most 5 new Layer 4 pattern articles per run to
`wiki/layer4/patterns/`. Hard quality gate:

1. Connection must not already appear in any existing `## Related
   Topics` section of the source articles.
2. Must have at least 2 independent pieces of evidence.
3. Must be statable in one sentence that would surprise a competent
   practitioner.

Uses Sonnet for writing. Prompt lives at
`prompts/conceptual_connections.md`.

### Mode B — Pattern Maturation  (Sunday 09:30 UTC)

Walks existing `wiki/layer4/patterns/*.md` articles. For each one
with `hypothesis: true`, counts new supporting and contradicting
evidence since `first_detected`, updates `supporting_evidence_count`
and `contradicting_evidence_count`, and recomputes `confidence` per
the standard evidence gradation. When confidence reaches `medium`+
and contradicting is 0, flips `hypothesis: false`. Pure Python — no
LLM calls. Uses synthesis versioning before any mutation.

### Mode C — Emergence Detection  (daily 09:00 UTC)

Lightweight signal watch. Reads new Layer 3 articles since the last
Mode C run. Does not write full Layer 4 articles. Logs candidate
patterns to `cache/layer4/emergence_candidates.json`. When a
candidate accumulates 3+ appearances across 2+ different topics, the
candidate is promoted to `synthesis_queue.json` as a `layer4_candidate`
so the next Mode A run picks it up. Uses Haiku (cheap, fast) for the
signal-detection pass.

### Mode D — Contradiction Resolution  (first Sunday of month, 10:00 UTC)

Reads all Layer 3 articles with non-empty `contradicting_sources`.
For each, attempts to explain the contradiction using the five-frame
framework (industry / size / timeline / methodology / context). If
explained, writes a resolution article to
`wiki/layer4/contradictions/` and adds a "Contradiction Resolved"
note to the source articles' `## Evolution and Change` sections
(via synthesis versioning). If not resolvable from internal evidence
alone, flags for web augmentation. Uses Sonnet for writing. Prompt
lives at `prompts/conceptual_contradictions.md`.

### Constraints

- **Conceptual agent never modifies Layer 2 or Layer 3 articles
  directly** except adding "Contradiction Resolved" notes via Mode D,
  which uses synthesis versioning so every edit is recoverable.
- **Layer 4 articles use their own frontmatter schema** — do not
  apply Layer 3 confidence rules to them. The evidence counts and
  confidence gradation are Layer-4 specific.
- **Mode A writes at most 5 articles per run.** Quality over quantity.
  If 5 strong connections aren't found, write fewer.
- **Dry-run mode is mandatory before the first real run of any mode.**
- **All operations appended to `wiki/log.md`** like every other agent.
- **All prompts live in `prompts/` files**, never hardcoded.
- **Engineering and interests namespaces are currently excluded**
  from Layer 4 analysis — the conceptual agent only reads
  `wiki/knowledge/` and `wiki/industries/` Layer 3 articles in
  this phase.
- **Registry enforcement:** Layer 4 articles reference only known
  topic/industry slugs in `topics_connected` and `industries_connected`.
- **The L3 map cache** must be invalidated correctly. Stale cache
  producing wrong connections is worse than slow cache regeneration.

### Layer 4 article schema (reprise)

See "Layer 4 articles" in the Frontmatter Schema section above for
the full template. Key fields the conceptual agent owns:
`concept_type`, `topics_connected`, `industries_connected`,
`confidence`, `hypothesis`, `supporting_evidence_count`,
`contradicting_evidence_count`, `status`, `first_detected`,
`last_updated`.
