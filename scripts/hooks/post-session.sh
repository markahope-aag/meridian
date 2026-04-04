#!/bin/bash
# Meridian post-session hook for Claude Code
# Installed to ~/.claude/hooks/post-session.sh by scripts/setup-machine.sh
# POSTs the session transcript to the Meridian receiver for capture.
# Fails silently — never interrupt a Claude Code session.

HOOK_DATA=$(cat)
TRANSCRIPT_PATH=$(echo "$HOOK_DATA" | jq -r '.transcript_path // empty')

# Exit silently if no transcript or receiver not configured
[ -z "$TRANSCRIPT_PATH" ] && exit 0
[ -z "$MERIDIAN_RECEIVER_URL" ] && exit 0
[ -z "$MERIDIAN_RECEIVER_TOKEN" ] && exit 0

curl -s -X POST "$MERIDIAN_RECEIVER_URL/capture/claude-session" \
  -H "Authorization: Bearer $MERIDIAN_RECEIVER_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"transcript_path\": \"$TRANSCRIPT_PATH\"}" \
  > /dev/null 2>&1 || true

exit 0
