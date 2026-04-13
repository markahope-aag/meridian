#!/bin/bash
# Daily ClientBrain → Meridian document sync. Runs at 00:00 UTC
# via VM crontab, one hour before the distill agent so fresh
# documents are captured before the pipeline starts.
#
# Pulls new emails, meetings (Fathom), Slack messages, and ClickUp
# tasks from ClientBrain's Supabase into capture/clientbrain/.
# Incremental: uses state/clientbrain-sync-state.json to only
# fetch documents newer than the last successful sync.
#
# Also runs the registry sync (pushes topics + industries to
# ClientBrain) so both systems stay aligned.
#
# Logs to /var/log/meridian-deploy/clientbrain-sync-<date>.log.

set -uo pipefail

LOG_DIR="${LOG_DIR:-/var/log/meridian-deploy}"
TODAY=$(date -u +%Y-%m-%d)
LOG_FILE="${LOG_DIR}/clientbrain-sync-${TODAY}.log"
TARGET_FQDN="${MERIDIAN_RECEIVER_FQDN:-meridian.markahope.com}"

mkdir -p "$LOG_DIR"

find_container_by_fqdn() {
    local want="$1"
    for cid in $(docker ps --format '{{.ID}}'); do
        if docker inspect "$cid" --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null \
            | grep -q "^COOLIFY_FQDN=${want}\$"; then
            echo "$cid"
            return
        fi
    done
}

{
    echo "=== $(date -u +%FT%TZ) ClientBrain sync run ==="
    cid=$(find_container_by_fqdn "$TARGET_FQDN")
    if [ -z "$cid" ]; then
        echo "ERROR: no container found matching FQDN '$TARGET_FQDN'"
        exit 1
    fi
    echo "target container: $cid ($TARGET_FQDN)"

    # Env vars — container may not have these, so pass explicitly
    CB_KEY="${MERIDIAN_CLIENTBRAIN_API_KEY:-Yjw5Cq8c7FwqpFmg-JbmNVZm7ipyUNI278Rnsm_SVEY}"
    CB_URL="${CLIENTBRAIN_URL:-https://client-brain.vercel.app}"

    # 1. Push registries (topics + industries) to ClientBrain
    echo "--- Registry sync (Meridian → ClientBrain) ---"
    docker exec -e "MERIDIAN_CLIENTBRAIN_API_KEY=$CB_KEY" -e "CLIENTBRAIN_URL=$CB_URL" \
        "$cid" python3 /meridian/scripts/sync-clientbrain-registry.py
    reg_exit=$?
    if [ $reg_exit -ne 0 ]; then
        echo "WARNING: registry sync exited with code $reg_exit"
    fi

    # 2. Pull documents from ClientBrain → Meridian capture
    echo "--- Document sync (ClientBrain → Meridian) ---"
    docker exec -e "MERIDIAN_CLIENTBRAIN_API_KEY=$CB_KEY" -e "CLIENTBRAIN_URL=$CB_URL" \
        "$cid" python3 /meridian/scripts/sync-clientbrain-documents.py
    doc_exit=$?
    if [ $doc_exit -ne 0 ]; then
        echo "ERROR: document sync exited with code $doc_exit"
    fi

    echo "=== $(date -u +%FT%TZ) ClientBrain sync finished ==="
} >> "$LOG_FILE" 2>&1

# Keep logs bounded (28-day retention)
find "$LOG_DIR" -type f -name 'clientbrain-sync-*.log' -mtime +28 -delete 2>/dev/null || true
