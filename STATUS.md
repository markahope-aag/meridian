# Meridian: Project Status

*Last updated: 2026-08-10*

## The Numbers

| Layer | Count |
|---|---|
| Business wiki files | 4,141 |
| Business knowledge topics | 67 (all 67 synthesized) |
| Business fragments | 2,836 |
| Engineering topics | 21 (20 synthesized) |
| Engineering fragments | 3,948 |
| Interests topics | 6 (no content yet) |
| Layer 4 articles | 265 |
| Concepts | 48 |
| Industries | 12 (all with content) |
| Client folders | 40 current, 6 former |
| Projects | 31 registered, 26 active |
| Commits ingested | 3,948 (classification queue empty) |
| Raw source docs | 7,102 |
| Git commits | 156 |

Live numbers are always at `/api/stats` and `/admin/`.

## Architecture: four knowledge namespaces

What began as three orthogonal dimensions over agency work is now four
namespaces with different sources and lifecycles.

| Namespace | Path | Source | Registry |
|---|---|---|---|
| **Business** | `wiki/clients/`, `wiki/knowledge/`, `wiki/industries/` | Client work | `clients.yaml`, `topics.yaml`, `industries.yaml` |
| **Engineering** | `wiki/engineering/<topic>/` | Git commit history | `engineering-topics.yaml`, `projects.yaml` |
| **Interests** | `wiki/interests/<topic>/` | Books, articles, reflections | `interests-topics.yaml` |
| **Layer 4** | `wiki/layer4/` | Cross-namespace synthesis | (derived) |

Within Business, a single insight is still cross-filed into clients,
topics, and industries simultaneously. All registries are manually
curated and compiler-enforced: agents can never invent a slug. New
entries arrive via the dashboard taxonomy review queue.

## Pipelines

### Business (client knowledge)

```
Fathom ──→ n8n ──────────→ /capture/fathom ──┐
ClientBrain sync ────────→ capture/clientbrain/ ┤
Google Drive ─→ Sieve ───→ /capture/gdrive ──┤
Claude Code hook ────────→ /capture/claude-session ┤
Manual CLI ──────────────→ /capture ─────────┘
                                              │
                          Daily Distill ◄─────┘  (100 files/run cap)
                                              │
                                            raw/
                                              │
                          Daily Compile ◄─────┘  (30 files/run cap)
                                              │
              wiki/clients/ + wiki/knowledge/ + wiki/industries/
                                              │
                          Layer 3 Synthesizer (5 topics/day)
                                              │
                          Evolution Detector (Sunday) ──→ drift queue
                                              │
                          Drift Re-synthesis (Sunday)
```

### Engineering (commit knowledge)

```
/var/repos/<slug>/ ──→ run-git-ingest.sh (hourly, host cron)
                                │
                    capture/external/commits/<project>/
                                │
                    classify-engineering-fragments.py (daily)
                    Haiku, batched 10 per call
                                │
                    wiki/engineering/<topic>/
                                │
                    Engineering synthesis rotation
```

## Automation

| Job | Schedule (UTC) | Runner |
|---|---|---|
| Fathom webhook | Real-time | n8n |
| ClientBrain sync | 00:00 daily | host cron |
| Daily Distill | 01:00 daily | n8n |
| Classify engineering commits | hourly, after ingest | `run-git-ingest.sh` |
| Daily Compile | 02:00 daily | n8n |
| Restic backup | 03:00 daily | host cron |
| Daily Synthesize | 04:00 daily | n8n |
| Conceptual Mode C | 05:00 daily | n8n |
| Weekly Lint | 06:00 Sunday | host cron |
| Evolution Detector | 07:00 Sunday | host cron |
| Conceptual Modes A + B | 08:00 Sunday | n8n |
| Drift Re-synthesis | Sunday, after detector | `run-evolution-detector.sh` |
| Conceptual Mode D | 10:00 1st Sunday | n8n |
| Git ingest | hourly | host cron |
| Watchdog | hourly at :15 | n8n |
| Admin stats collector | every 15 min | host cron |
| VM auto-deploy | every minute | host cron |

## Infrastructure

| Component | Notes |
|---|---|
| Receiver (`meridian.markahope.com`) | Flask + gunicorn, code bind-mounted from `/meridian/receiver/`, HUP on push |
| Dashboard (`brain.markahope.com`) | Coolify-built image, `web/` hot-patched on push. **Password protected.** |
| n8n (`auto.asymmetric.pro`) | 8 active Meridian workflows |
| `/meridian/` git checkout | Auto-pulls `main` every minute |
| Restic + Cloudflare R2 | Nightly, encrypted, 7d/4w/12m retention |
| SQLite job store | `/meridian/state/jobs.db` |
| VM | Hetzner, 150 GB disk, 7.7 GB RAM |

### Dashboard authentication

The dashboard requires a password (`MERIDIAN_DASHBOARD_PASSWORD` on the
Brain container). The gate **fails closed**: if the variable is missing,
every route returns 503 rather than serving the wiki anonymously. This is
deliberate. The variable was absent once and the entire wiki, including
40 client records, was served publicly. `MERIDIAN_DASHBOARD_ALLOW_ANONYMOUS=1`
is the explicit opt-out for local development. Covered by `tests/test_auth.py`.

Note that Cloudflare serves a managed `robots.txt` at the edge which
overrides the app's own `Disallow: /`. Auth is what actually protects the
content; `X-Robots-Tag: noindex` is set on every response as a backstop.

## Receiver endpoints

| Endpoint | Purpose |
|---|---|
| `POST /capture` | Generic markdown capture (1 MB cap) |
| `POST /capture/fathom` | Fathom meeting webhook, dedup by recording_id |
| `POST /capture/claude-session` | Claude Code session transcript |
| `POST /capture/gdrive` | Google Drive ingestion, dedup + 1 MB cap |
| `POST /distill` | Daily Distill (default limit 100) |
| `POST /compile` | Compiler (default limit 30) |
| `POST /classify` | Engineering classifier (default limit 250) |
| `POST /lint` | Linter |
| `POST /synthesize` | Synthesize one topic or industry |
| `POST /synthesize/schedule` | Drain synthesis queue, accepts `queued_by` |
| `GET /synthesize/queue` | Queue status |
| `POST /conceptualize` | Layer 4 conceptual agent (modes A to D) |
| `POST /watchdog` | Detect and repair stuck pipeline items |
| `POST /ask` | Q&A against the wiki |
| `POST /debrief` | Debrief a Claude Code session |
| `POST /context` | Search wiki, return context brief |
| `GET /jobs/<id>` | Poll async job status |
| `GET /health` | Health check |

All pipeline endpoints return 202 + job_id by default. Add `?sync=true` to
block. Writes require `Authorization: Bearer $MERIDIAN_RECEIVER_TOKEN`.

## Agents and scripts

| Agent | Purpose |
|---|---|
| `daily_distill.py` | Always-promote; Sieve handles review upstream |
| `compiler.py` | Two-pass plan + write, cross-files into three dimensions |
| `synthesizer.py` | Layer 3 synthesis with extract cache + versioning |
| `synthesis_scheduler.py` | Drains `synthesis_queue.json` |
| `conceptual_agent.py` | Layer 4 emergence, connections, contradictions |
| `evolution_detector.py` | Drift detection, auto-queues re-synthesis |
| `linter.py` | Dimension-aware wiki health check |
| `watchdog.py` | Repairs stuck pipeline state |
| `qa_agent.py` | Research and answer with citations |

Host-side runners live in `scripts/run-*.sh`. Each logs to
`/var/log/meridian-deploy/` with 28-day retention.

## Key design decisions

- **Fail-closed auth** on the dashboard. A config mistake must never be
  what opens the door.
- **No credentials in the repo.** ClientBrain's key reads from
  `/etc/meridian-clientbrain.env`, not a shell default.
- **Registry-enforced compiler.** No agent invents a slug.
- **Two-pass compiler**: Haiku plans, Sonnet writes, 3 parallel workers.
- **Two-pass synthesizer** with an extraction cache, so prompt iterations
  re-run only the write pass.
- **Synthesis output versioning**: every overwrite keeps the prior version
  under `state/synthesis_versions/`.
- **Per-run caps** on distill, compile, and classify so a scheduled run
  cannot exceed its subprocess timeout.
- **Ownership handoff**: host cron chowns ingested fragments to the
  container user, because the classifier runs inside the container and has
  to rewrite and move them.
- **Consumers ride with their producers.** Classification runs at the end
  of the ingest cron, and drift re-synthesis at the end of the evolution
  cron, rather than as separate scheduled jobs. Both of those consumers
  were previously unscheduled and the work piled up silently for months:
  ~2,900 commit fragments and every drift detection ever queued. One cron
  entry that does both halves cannot drift out of sync with itself.
- **Credentials come from 1Password, not disk.** Operator secrets live in
  the `meridian.env` document in the `DevBox .env Files` vault, matching
  the convention every other project here already uses.
  `scripts/load-secrets.sh` pipes them straight into the environment, so
  no plaintext copy is written. The 1Password service account token is
  the one credential that has to exist locally.
- **All execution on the VM.** The CLI and hooks are HTTP clients.
- **Prompts as files** in `prompts/`, never hardcoded.
- **Git-based deploy**: `/meridian/` is a checkout of `main`, auto-pulled
  every minute. Checkpoint-protected files survive `git reset --hard`.

## Known gaps

- **Interests namespace is empty.** 6 topics registered, no ingestion path
  wired up yet.
- **Engineering Layer 3 is 20 of 21 topics.**
- **Layer 4 reports 0 patterns** against 90 emerging signals and 30
  pending candidates.
- **Compile backlog is unmeasured.** `raw/` holds 7,102 files, but
  compiled ones stay there marked with `compiled_at`, so the uncompiled
  count is not exposed in stats. Distill promotes up to 100/day while
  compile consumes 30/day, so the gap is worth watching.
- **ClientBrain capture queue sits at ~2,000.** Distill drains 100/day
  from it.

## Repo

`github.com/markahope-aag/meridian` (public), branch `main`, 156 commits.
The repo holds code only. All content and pipeline state live on the VM
and are never committed.
