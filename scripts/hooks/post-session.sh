#!/bin/bash
# Meridian post-session hook for Claude Code (bash / git-bash / macOS / Linux).
# Installed to ~/.claude/hooks/post-session.sh by scripts/setup-machine.sh.
#
# Reads the local Claude Code transcript and POSTs its content to the
# Meridian receiver. The receiver lives on a remote VM and cannot read
# this machine's filesystem, so we send the JSONL inline.
#
# Fails silently — never interrupt a Claude Code session.
# Requires: jq, curl.

HOOK_DATA=$(cat)
TRANSCRIPT_PATH=$(echo "$HOOK_DATA" | jq -r '.transcript_path // empty')

[ -z "$TRANSCRIPT_PATH" ] && exit 0
[ -z "$MERIDIAN_RECEIVER_URL" ] && exit 0
[ -z "$MERIDIAN_RECEIVER_TOKEN" ] && exit 0
[ ! -f "$TRANSCRIPT_PATH" ] && exit 0

# Build payload with file content. --rawfile reads the JSONL verbatim
# and lets jq handle the JSON escaping safely.
PAYLOAD=$(jq -n \
    --arg path "$TRANSCRIPT_PATH" \
    --rawfile content "$TRANSCRIPT_PATH" \
    '{transcript_path: $path, transcript_jsonl: $content}' 2>/dev/null)

[ -z "$PAYLOAD" ] && exit 0

echo "$PAYLOAD" | curl -s -X POST "$MERIDIAN_RECEIVER_URL/capture/claude-session" \
    -H "Authorization: Bearer $MERIDIAN_RECEIVER_TOKEN" \
    -H "Content-Type: application/json; charset=utf-8" \
    --data-binary @- \
    --max-time 60 \
    > /dev/null 2>&1 || true

exit 0
