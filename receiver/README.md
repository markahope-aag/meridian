# Meridian Receiver

Central API for the Meridian knowledge system. Deployed on Coolify with a bind mount
to `/meridian/` on the host filesystem.

The receiver is a thin Flask/Gunicorn app. All code it executes (agents, prompts, tools)
lives on the host at `/meridian/` and is read via the bind mount — not baked into the image.
This means updates to agents or prompts take effect immediately without rebuilding.

## Endpoints

| Endpoint | Auth | Purpose |
|---|---|---|
| `GET /health` | No | Health check |
| `POST /capture` | Bearer | Write generic .md to `capture/` |
| `POST /capture/fathom` | Bearer | Format Fathom meeting webhook → `capture/` |
| `POST /capture/claude-session` | Bearer | Convert Claude Code JSONL transcript → `capture/` |
| `POST /distill` | Bearer | Run Daily Distill agent (score and promote capture → raw) |
| `POST /compile` | Bearer | Run Compiler agent (compile raw → wiki) |
| `POST /ask` | Bearer | Q&A against the wiki |
| `POST /debrief` | Bearer | Debrief a Claude Code session |
| `POST /context` | Bearer | Search wiki, return context brief |

## Deploy on Coolify

### 1. Create the host directory

SSH into the Hetzner server and create the full Meridian directory tree:

```bash
ssh root@178.156.209.202
mkdir -p /meridian/{capture,raw,wiki/{concepts,articles,categories,dev/{patterns,decisions,dead-ends}},outputs,tools,agents,prompts,scripts}
```

Copy the project files to `/meridian/` on the server. Everything except `receiver/`
and `cli/` needs to be on the host — the receiver reads it all via bind mount:

```bash
rsync -av --exclude='receiver/' --exclude='cli/' --exclude='.git/' \
  ./  root@178.156.209.202:/meridian/
```

### 2. Create the Coolify application

In Coolify dashboard (https://app.coolify.io):

1. **New Resource → Application → Dockerfile**
2. Point to this repository (or upload the Dockerfile)
3. Set the **build context** to `receiver/`
4. Set the **Dockerfile path** to `Dockerfile` (relative to build context)

### 3. Configure the bind mount (required)

In the application settings under **Storages / Volumes**, add:

```
Host path:      /meridian
Container path: /meridian
```

**This is required.** The receiver reads all agents, prompts, config, and wiki content
from `/meridian/` at runtime. Without this mount, only `/health` will work.

### 4. Set environment variables

In the application settings under **Environment Variables**:

| Variable | Value | Description |
|---|---|---|
| `MERIDIAN_RECEIVER_TOKEN` | (generate a strong token) | Bearer auth for all endpoints |
| `ANTHROPIC_API_KEY` | (your Anthropic key) | Used by agents for LLM calls |
| `MERIDIAN_ROOT` | `/meridian` | Root directory (default, usually no need to change) |

### 5. Configure domain

Set the domain to `meridian.markahope.com` in the application settings.
Coolify handles TLS via Let's Encrypt.

Point the DNS A record for `meridian.markahope.com` to `178.156.209.202`.

### 6. Deploy

Click Deploy. Check the health endpoint:

```bash
curl https://meridian.markahope.com/health
```

## Register the Fathom Webhook

Once the receiver is deployed, register a Fathom webhook to send meeting
transcripts to `/capture/fathom`.

### Option A: Direct (Fathom → receiver)

```bash
curl -X POST https://api.fathom.ai/external/v1/webhooks \
  -H "X-Api-Key: YOUR_FATHOM_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "destination_url": "https://meridian.markahope.com/capture/fathom",
    "triggered_for": ["my_recordings", "shared_external_recordings"],
    "include_transcript": true,
    "include_summary": true,
    "include_action_items": true,
    "include_crm_matches": false
  }'
```

Save the returned `secret` for webhook signature verification (future enhancement).

### Option B: Via n8n (recommended)

Route through n8n for visibility:

1. Create an n8n Webhook node (trigger)
2. Point Fathom webhook to the n8n webhook URL
3. n8n HTTP Request node forwards the payload to `https://meridian.markahope.com/capture/fathom`
   with `Authorization: Bearer <MERIDIAN_RECEIVER_TOKEN>`

This gives you every webhook execution in n8n's log.

## Test Each Endpoint

```bash
TOKEN="your-token-here"
URL="https://meridian.markahope.com"

# Health (no auth)
curl $URL/health

# Generic capture
curl -X POST $URL/capture \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"title": "Test Note", "content": "This is a test capture."}'

# Fathom webhook (simulated)
curl -X POST $URL/capture/fathom \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "recording_id": 12345,
    "title": "Test Meeting",
    "url": "https://fathom.video/r/12345",
    "share_url": "https://fathom.video/s/12345",
    "created_at": "2026-04-04T10:00:00Z",
    "transcript": [{"speaker": {"display_name": "Mark"}, "text": "Hello", "timestamp": "00:00:01"}],
    "default_summary": {"markdown_formatted": "Test summary"},
    "action_items": [{"description": "Follow up", "completed": false}],
    "calendar_invitees": [{"name": "Mark", "email": "mark@test.com"}]
  }'

# Claude session capture (JSONL file must exist on the server)
curl -X POST $URL/capture/claude-session \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"transcript_path": "/path/to/session.jsonl"}'

# Distill (dry-run)
curl -X POST $URL/distill \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"mode": "dry-run"}'

# Compile all uncompiled raw docs
curl -X POST $URL/compile \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{}'

# Ask
curl -X POST $URL/ask \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"question": "What is Meridian?"}'

# Debrief (most recent session)
curl -X POST $URL/debrief \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{}'

# Context
curl -X POST $URL/context \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"topic": "authentication"}'
```

## Machine Setup

Run `scripts/setup-machine.sh` on each machine to:
- Install the `meridian` CLI (`pip install -e ./cli`)
- Copy the Claude Code post-session hook to `~/.claude/hooks/`
- Register the hook in `~/.claude/settings.json` (merges safely, preserves existing settings)
- Configure `~/.meridian/config.yaml` with receiver URL and token
- Export env vars to your shell profile

```bash
cd /path/to/meridian
bash scripts/setup-machine.sh
```
