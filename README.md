# Meridian — LLM-Powered Personal Knowledge System

## What It Is

Meridian is a personal knowledge system where an LLM incrementally builds and maintains a wiki from raw source documents. Instead of using RAG (retrieve chunks at query time, forget everything between queries), Meridian has the LLM permanently compile knowledge into interlinked markdown articles that compound over time. The human curates sources and asks questions. The LLM does all the bookkeeping.

Inspired by Andrej Karpathy's concept of an LLM-maintained wiki.

## How It Works

Documents flow through a three-stage pipeline:

```
capture/ → (Daily Distill) → raw/ → (Compiler) → wiki/
```

**Stage 1 — Capture.** Everything lands in `capture/` unfiltered. Sources include:
- **Fathom meetings** — a webhook fires when a meeting ends, n8n forwards the transcript and summary to the receiver, which formats it as markdown
- **Obsidian Web Clipper** — clip any article from the browser, Syncthing delivers it to the VM
- **Claude Code sessions** — a post-session hook automatically captures every coding session transcript
- **Manual drops** — `meridian capture --url`, `--file`, or `--text` from any machine
- **Direct file drops** — anything placed in the Syncthing-synced folder

**Stage 2 — Daily Distill.** An LLM agent reviews `capture/` daily (6 AM via n8n), scores each document on relevance (0-10) and quality (0-10), and promotes worthy items to `raw/` with normalized frontmatter. During bootstrap (<20 wiki articles), the threshold is 6+. In steady state, it's 8+ for auto-promote, 6-7 for human approval.

**Stage 3 — Compiler.** A two-pass LLM pipeline compiles raw documents into wiki articles. Pass 1 (Haiku) plans where to file — detecting clients, inferring status, identifying transferable learnings. Pass 2 (Sonnet) writes the actual content with 3 concurrent workers. Index and backlinks update once after all workers complete. A single meeting may produce client articles, knowledge extractions, and cross-links. Target: 5 documents in under 60 seconds.

## Architecture

All execution happens on a Hetzner VM managed by Coolify. Clients are thin HTTP wrappers.

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
│  │  POST /distill       │                                │
│  │  POST /compile       │                                │
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
              │ Syncthing
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

**meridian-receiver** — A Flask/Gunicorn service deployed on Coolify with a bind mount to `/meridian/`. This is the brain. Every write to the filesystem and every agent execution goes through it. Endpoints:

| Endpoint | Purpose |
|---|---|
| `POST /capture` | Write any markdown to `capture/` |
| `POST /capture/fathom` | Format Fathom meeting webhook payload |
| `POST /capture/claude-session` | Convert Claude Code JSONL transcript |
| `POST /distill` | Run the Daily Distill agent |
| `POST /compile` | Run the Compiler agent |
| `POST /ask` | Q&A against the wiki |
| `POST /debrief` | Debrief a Claude Code session |
| `POST /context` | Search wiki, return context brief |
| `GET /health` | Health check |

All endpoints except `/health` require bearer token auth.

**meridian CLI** — A pip-installable Python package (`pip install -e ./cli`) that wraps the receiver API. Commands: `meridian ask`, `debrief`, `context`, `capture`, `status`. Works identically on any machine. Reads `~/.meridian/config.yaml` for the receiver URL and token.

**n8n** — Event-driven triggers. Two workflows:
- **Fathom webhook** — Fathom fires `new-meeting-content-ready` → n8n receives it → forwards to receiver `/capture/fathom`
- **Daily Distill** — Schedule trigger at 6 AM → calls receiver `/distill`

**Syncthing** — Syncs the entire `/meridian/` directory from the VM to every machine in real-time. Runs as a systemd service on the VM, as a background app on laptops.

**Obsidian** — Local viewer. Each machine opens the Syncthing-synced `/meridian/` folder as an Obsidian vault. Graph view shows the wiki structure.

**Claude Code hooks** — A post-session hook (`~/.claude/hooks/post-session.sh`) fires on every Claude Code `Stop` event, POSTing the session transcript to the receiver. The debrief agent extracts architectural decisions, patterns that worked, dead ends, and open questions.

## Directory Structure

```
/meridian/
├── AGENTS.md          # source of truth — every agent reads this first
├── README.md          # this file
├── capture/           # unfiltered intake
├── raw/               # promoted source docs with normalized frontmatter
├── wiki/              # LLM-maintained knowledge base
│   ├── _index.md      # master index
│   ├── _backlinks.md  # backlink registry
│   ├── log.md         # append-only operations log
│   ├── concepts/      # concept explainers
│   ├── articles/      # source summaries and analyses
│   ├── categories/    # emergent category pages
│   ├── clients/       # per-client folders organized by status
│   │   ├── current/   # active client engagements
│   │   ├── former/    # completed engagements
│   │   └── prospects/ # potential clients
│   ├── knowledge/     # transferable learnings by topic
│   └── dev/           # Claude Code learnings
│       ├── patterns/  # reusable approaches
│       ├── decisions/ # architectural choices
│       └── dead-ends/ # things that failed
├── outputs/           # reports, slides, charts
├── agents/            # Python agent scripts
│   ├── daily_distill.py
│   ├── compiler.py
│   └── debrief.py
├── prompts/           # LLM system prompts (never hardcoded)
├── receiver/          # Flask API service
├── cli/               # pip-installable CLI
├── n8n/               # importable n8n workflow JSONs
├── scripts/           # setup and hook scripts
├── tools/             # CLI utility scripts
└── config.yaml        # paths and settings (no secrets)
```

## Key Design Decisions

**AGENTS.md is the source of truth.** Every agent reads it before doing anything. It contains the wiki schema, directory conventions, filing rules, frontmatter spec, and protocol for each agent. Prompts reference it rather than duplicating rules.

**`_index.md` is the most critical file.** The compiler reads it before every filing decision to understand what already exists. This is what makes consistent decisions possible across hundreds of compilations.

**Bootstrap vs. steady state.** The first 20 wiki articles require more permissive thresholds and include proposals explaining why the compiler filed where it did. After 20 articles, the wiki has a clear shape and the agents file autonomously.

**All execution on the VM.** The CLI, hooks, and n8n are all just HTTP clients that call the receiver. This means every machine works identically — no local dependencies beyond the thin CLI.

**Prompts as files.** All LLM system prompts live in `prompts/*.md`, never hardcoded in Python scripts. This makes them easy to iterate on without touching code.

**Append-only operations log.** `wiki/log.md` records every agent action with a consistent format (`## [date] operation | description`). Gives a timeline of how the wiki evolved.

**Dynamic client detection.** The compiler detects client references from document content — attendee names, email domains, contextual phrases — rather than maintaining a static client list. New clients are flagged for human approval. Client status (current/former/prospect) is inferred from context.

**Cross-filing with knowledge extraction.** Client-specific documents are filed under `wiki/clients/`, but transferable learnings are also extracted to `wiki/knowledge/` with backlinks in both directions. Knowledge compounds across clients.

## Agents

| Agent | Script | Trigger | Purpose |
|---|---|---|---|
| Daily Distill | `agents/daily_distill.py` | n8n schedule (6 AM) | Score capture docs, promote to raw |
| Compiler | `agents/compiler.py` | POST `/compile` | Compile raw docs into wiki articles |
| Debrief | `agents/debrief.py` | POST `/debrief` | Extract learnings from Claude Code sessions |
| Q&A | `agents/qa_agent.py` | POST `/ask` | Answer questions against the wiki |
| Linter | `agents/linter.py` | TBD | Consistency checks and gap detection |

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
| LLM | Claude via Anthropic API |
| Workflow automation | n8n |
| File sync | Syncthing |
| Local viewer | Obsidian |
| Meeting capture | Fathom |
| Session capture | Claude Code hooks |
| CLI | Python, requests |
| Repo | github.com/markahope-aag/meridian |
