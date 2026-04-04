# Meridian — Agent Protocol

> Every LLM agent entering this repository MUST read this file first.
> This is the single source of truth for schema, conventions, and filing rules.

## Project Overview

Meridian is an LLM-maintained personal knowledge base. Documents flow through a pipeline:

```
capture/ → (Daily Distill) → raw/ → (Compiler) → wiki/
```

Humans rarely edit `wiki/` directly. The LLM owns it.

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
| **meridian-receiver** | Coolify container (bind mount to `/meridian/`) | Central API — all writes and agent execution |
| **meridian CLI** | Any machine (`pip install -e .`) | Thin HTTP client — `meridian ask`, `meridian debrief`, `meridian context` |
| **n8n** | Coolify container | Event-driven triggers (Fathom webhooks, scheduled distill/lint) |
| **Obsidian Sync** | VM ↔ all machines | Syncs entire `/meridian/` tree for local viewing |
| **Claude Code hooks** | Any machine | Post-session debrief via HTTP to receiver |

### Auth

- All receiver endpoints require `Authorization: Bearer <MERIDIAN_RECEIVER_TOKEN>`
- Token is set as a Coolify env var on the receiver container
- Each local machine stores the token in `~/.meridian/config.yaml`
- n8n includes the token in HTTP Request node headers

### Local config (`~/.meridian/config.yaml`)

```yaml
receiver_url: https://meridian.markahope.com
token: <MERIDIAN_RECEIVER_TOKEN>
```

## Directory Layout

```
/meridian/
├── AGENTS.md          ← you are here
├── capture/           # unfiltered intake — everything lands here first
├── raw/               # promoted source docs with normalized frontmatter
├── wiki/              # LLM-maintained knowledge base
│   ├── _index.md      # master index — ALWAYS read before filing
│   ├── _backlinks.md  # auto-maintained backlink registry
│   ├── log.md         # append-only operations log
│   ├── concepts/      # concept explainers (one concept per file)
│   ├── articles/      # summaries and analyses of source material
│   ├── categories/    # category index pages
│   ├── clients/       # per-client folders, organized by status
│   │   ├── current/   # active client engagements
│   │   │   └── [name]/
│   │   │       └── _index.md
│   │   ├── former/    # completed engagements
│   │   │   └── [name]/
│   │   │       └── _index.md
│   │   └── prospects/ # potential clients
│   │       └── [name]/
│   │           └── _index.md
│   ├── knowledge/     # transferable learnings by topic
│   │   ├── _index.md  # knowledge topic index
│   │   └── [topic]/   # e.g. paid-social/, seo-strategy/
│   └── dev/           # Claude Code learnings
│       ├── patterns/  # reusable approaches that worked
│       ├── decisions/ # architectural choices and reasoning
│       └── dead-ends/ # things that failed and why
├── outputs/           # reports, slides, charts filed back by agents
├── tools/             # CLI scripts (used by humans and agents)
├── agents/            # agent loop scripts
├── prompts/           # all LLM system prompts as .md files
├── receiver/          # meridian-receiver HTTP service (deployed on Coolify)
│   ├── app.py         # Flask app — capture, ask, debrief, context endpoints
│   ├── Dockerfile     # production image with gunicorn
│   └── README.md      # deployment and Fathom webhook setup
├── cli/               # thin meridian CLI (pip-installable)
│   ├── pyproject.toml
│   └── meridian_cli/
│       ├── __init__.py
│       └── main.py
├── scripts/           # setup and hook scripts
│   ├── setup-machine.sh   # one-time machine onboarding
│   └── hooks/
│       └── post-session.sh # Claude Code post-session hook
└── config.yaml        # paths and settings (no secrets)
```

## Receiver API

All endpoints require `Authorization: Bearer <token>`.

### Capture endpoints

| Endpoint | Method | Purpose |
|---|---|---|
| `POST /capture` | Generic | Write any `.md` payload to `capture/` |
| `POST /capture/fathom` | Fathom-specific | Format `new-meeting-content-ready` webhook payload as `.md` |
| `POST /capture/claude-session` | Claude Code | Convert JSONL session transcript to `.md` in `capture/` |

### Pipeline endpoints (async)

These return `202 Accepted` with a `job_id` by default. Poll `GET /jobs/<id>` for results.
Add `?sync=true` for synchronous execution (blocks until complete).

| Endpoint | Purpose |
|---|---|
| `POST /distill` | Run Daily Distill — score capture docs, promote to raw |
| `POST /compile` | Run Compiler — compile raw docs into wiki articles |
| `GET /jobs/<id>` | Poll job status: `running`, `completed`, or `failed` |

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

### Capture documents (`capture/`)

Capture docs have minimal or no frontmatter — they arrive in whatever format the source
provides. The Daily Distill agent normalizes them when promoting to `raw/`.

## Filing Rules

1. **Always read `wiki/_index.md` first.** Before creating or modifying any wiki article,
   read the full index to understand the current structure and avoid duplicates.

2. **One concept per file.** Don't create monolithic pages. If a topic has distinct
   subtopics, each gets its own file with cross-links.

3. **File names are kebab-case.** Examples: `transformer-architecture.md`,
   `attention-mechanism.md`, `2026-04-04-team-standup.md`.

4. **Backlinks are mandatory.** When article A references article B, both files must
   reflect the link. Update `_backlinks.md` accordingly.

5. **Categories are emergent.** Don't pre-create categories. When 3+ articles share a
   theme, create a category page that links to them.

6. **Incremental updates only.** Never rewrite an existing article from scratch unless
   explicitly asked. Append, amend, or create a new related article instead.

7. **Source attribution.** Every wiki article must list its source documents in the
   `source_docs` frontmatter field.

8. **Update `_index.md` after every write.** Any time you create or modify a wiki article,
   update the index to reflect the change.

9. **Append to `wiki/log.md` after every operation.** Every agent must log what it did.

10. **Client detection.** The compiler detects client references dynamically — no static
    list. Signals to look for:
    - Company names in context of "our client", "the client", "we're working with",
      "their campaign", "their account"
    - Names appearing in meeting attendee lists from Fathom
    - Email domains of meeting participants (e.g. `@acme.com` → Acme)
    - Recurring named entities across multiple documents
    
    On detecting a **new client** not yet in the wiki, the compiler flags it for approval:
    `"new_client": {"name": "Acme Corp", "slug": "acme", "status": "current"}`
    The calling agent creates the folder after human confirmation.

11. **Client status.** The compiler infers status from context:
    - **Current** — active campaigns, recent meetings, ongoing work, present tense
    - **Former** — past tense, "when we worked with", closed projects, no recent activity
    - **Prospect** — proposal language, discovery calls, "potential", RFP references
    
    File under `wiki/clients/current/`, `wiki/clients/former/`, or `wiki/clients/prospects/`
    accordingly. Use lowercase hyphenated folder names (e.g. "Acme Corp" → `acme`).

12. **Client status transitions.** If the compiler sees signals that a client's status has
    changed (e.g. "we've wrapped up with X", or a prospect becomes a client), it flags
    the transition for review rather than moving the folder automatically:
    `"status_change": {"client": "acme", "from": "current", "to": "former", "signal": "..."}`

13. **Transferable learning detection.** If a document contains insights applicable beyond
    one client — platform strategies, channel learnings, what works/doesn't work, industry
    patterns — also create or update a page in `wiki/knowledge/[topic]/`. Topics use
    kebab-case (e.g. `paid-social`, `seo-strategy`, `pitch-deck-structure`).

14. **Cross-filing.** When both client and knowledge apply, file in `wiki/clients/` AND
    extract the transferable learning to `wiki/knowledge/`. Add backlinks in both
    directions so client pages reference the general knowledge and knowledge pages
    reference the client examples.

15. **Client index.** Each `wiki/clients/[status]/[name]/_index.md` is maintained by the
    compiler and must track:
    - Status (current / former / prospect)
    - First seen date
    - Last activity date
    - Key contacts (extracted from meeting attendees)
    - Active projects
    - Links to all related docs and knowledge/ extractions

16. **Knowledge index.** `wiki/knowledge/_index.md` lists all knowledge topics with
    one-line summaries, maintained by the compiler. Updated every time a knowledge
    page is created or modified.

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
| `distill` | daily_distill | Document scored and promoted/skipped |
| `compile` | compiler | Raw document compiled into wiki article(s) |
| `query` | qa_agent | Question answered against the wiki |
| `lint` | linter | Consistency check or gap identified |

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

**Pass 1 — Planning (Haiku, fast):** Reads the raw document and `wiki/_index.md`,
decides what files to create/update and where. Detects clients, infers status,
identifies transferable learnings. Returns a filing plan as JSON.

**Pass 2 — Writing (Sonnet, parallel):** Takes the plan and writes each wiki file
concurrently (3 workers). Each worker gets the raw document and one plan entry.

**After all files:** Index and backlinks are updated once in a batch.

Multiple documents compile in parallel (3 concurrent). Target: 5 documents < 60 seconds.

### Compilation steps

1. Load `wiki/_index.md` once (read-only context for all workers)
2. For each raw document, run Pass 1 (planning)
3. For each plan entry, run Pass 2 (writing) in parallel
4. Mark each raw document as compiled (`compiled_at` in frontmatter)
5. Batch-update `wiki/_index.md` and `wiki/_backlinks.md` after all workers complete
6. Append to `wiki/log.md`

### Source type routing

| Source type | Destination | Notes |
|---|---|---|
| article, paper, note | `wiki/articles/` or `wiki/concepts/` | Standard routing |
| meeting | `wiki/articles/` | Extract decisions + action items as cross-links |
| claude-session | `wiki/dev/` | Route by content type (see below) |
| any (client-specific) | `wiki/clients/[status]/[name]/` | If document mentions a client (see detection rules) |
| any (transferable) | `wiki/knowledge/[topic]/` | If insights apply beyond one client |

**Client and knowledge routing is additive** — a single document may produce files in
`wiki/articles/` AND `wiki/clients/` AND `wiki/knowledge/`. The compiler should always
check for client references and transferable learnings regardless of source type.

### Claude Code session routing (`claude-session`)

Session debriefs contain structured sections. Route each to the appropriate `wiki/dev/` subdirectory:

| Content | Destination | Filename pattern |
|---|---|---|
| Architectural decisions | `wiki/dev/decisions/` | `decision-{slug}.md` |
| Reusable patterns | `wiki/dev/patterns/` | `pattern-{slug}.md` |
| Failed approaches | `wiki/dev/dead-ends/` | `dead-end-{slug}.md` |

A single debrief may produce multiple files. Each decision, pattern, or dead-end gets its own file.

## Daily Distill Protocol

When reviewing `capture/` for promotion to `raw/`:

1. Read every new/unprocessed file in `capture/`
2. Score each on relevance (0-10) and quality (0-10)
3. For items scoring 6+ on both: propose promotion to `raw/` with normalized frontmatter
4. During bootstrap: send proposal for human approval (via n8n email)
5. During steady state (20+ wiki articles): auto-promote for scores >= 8, propose for 6-7
6. During bootstrap (<20 wiki articles): auto-promote for scores >= 6 (more permissive to seed the wiki)
6. Never delete from `capture/` — mark processed items by adding frontmatter:
   `distill_status: promoted | skipped`
   `distill_date: "2026-04-04"`
   `distill_score: { relevance: 8, quality: 7 }`
