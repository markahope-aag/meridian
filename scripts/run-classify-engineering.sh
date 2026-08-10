#!/bin/bash
# Drain the engineering commit-classification queue.
#
# scripts/run-git-ingest.sh runs hourly and writes commit fragments to
# capture/external/commits/<project>/. Nothing consumed them, so the queue
# grew to roughly 2,900 fragments and the weekly linter reported the same
# "Untouched Capture Fragments" block every Sunday for months. This script
# is the missing consumer.
#
# Runs inside the receiver container rather than on the host, because that
# is where the anthropic package and ANTHROPIC_API_KEY live.
#
# Designed to run daily via host cron, after the hourly ingest:
#
#   30 1 * * *  /bin/bash /meridian/scripts/run-classify-engineering.sh
#
# Configuration:
#   MERIDIAN_CLASSIFY_LIMIT   fragments per run (default 250, 0 = unlimited)
#   MERIDIAN_CLASSIFY_PROJECT limit to a single project slug
#   MERIDIAN_RECEIVER_FQDN    container to exec into
#
# Logs to /var/log/meridian-deploy/classify-engineering-<date>.log.

set -uo pipefail

LOG_DIR="${LOG_DIR:-/var/log/meridian-deploy}"
TODAY=$(date -u +%Y-%m-%d)
LOG_FILE="${LOG_DIR}/classify-engineering-${TODAY}.log"
TARGET_FQDN="${MERIDIAN_RECEIVER_FQDN:-meridian.markahope.com}"
LIMIT="${MERIDIAN_CLASSIFY_LIMIT:-250}"
PROJECT="${MERIDIAN_CLASSIFY_PROJECT:-}"
CLASSIFY_SCRIPT="/meridian/scripts/classify-engineering-fragments.py"

mkdir -p "$LOG_DIR"

# Only one classify run at a time. The daily cron and a manual backfill
# would otherwise race for the same fragments, and two processes moving
# the same file produces spurious failures.
LOCK_FILE="/tmp/meridian-classify.lock"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    echo "$(date -u +%FT%TZ) another classify run is in progress, skipping" >> "$LOG_FILE"
    exit 0
fi

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
    echo "=== $(date -u +%FT%TZ) engineering classify run ==="
    cid=$(find_container_by_fqdn "$TARGET_FQDN")
    if [ -z "$cid" ]; then
        echo "ERROR: no container found matching FQDN '$TARGET_FQDN'"
        exit 1
    fi
    echo "target container: $cid  limit=$LIMIT  project=${PROJECT:-<all>}"

    cmd=(python3 "$CLASSIFY_SCRIPT")
    if [ "$LIMIT" -gt 0 ] 2>/dev/null; then
        cmd+=(--limit "$LIMIT")
    fi
    if [ -n "$PROJECT" ]; then
        cmd+=(--project "$PROJECT")
    fi

    docker exec "$cid" "${cmd[@]}"
    classify_exit=$?
    if [ $classify_exit -ne 0 ]; then
        echo "ERROR: classifier exited with code $classify_exit"
    fi

    echo "=== $(date -u +%FT%TZ) classify run finished (exit=$classify_exit) ==="
} >> "$LOG_FILE" 2>&1

# Keep logs bounded (28-day retention).
find "$LOG_DIR" -type f -name 'classify-engineering-*.log' -mtime +28 -delete 2>/dev/null || true
