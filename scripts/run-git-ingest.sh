#!/bin/bash
# VM-side git ingestion — keeps the engineering namespace fresh.
#
# For every clone under $MERIDIAN_REPO_ROOT, runs `git fetch --all --prune`
# and then invokes scripts/ingest-git-history.py to write fragments for any
# new commits to /meridian/capture/external/commits/<slug>/.
#
# Idempotent: ingest-git-history.py skips commit fragments that already
# exist on disk. Re-running is safe and cheap.
#
# Designed to run hourly via host cron. Authentication is whatever git
# is set up to use on this VM (SSH key in /root/.ssh/ is the default).
#
# Configuration:
#   MERIDIAN_REPO_ROOT      directory holding <slug>/ checkouts (default /var/repos)
#   MERIDIAN_INGEST_SINCE   git --since= window (default "24 hours ago")

set -uo pipefail

REPO_ROOT="${MERIDIAN_REPO_ROOT:-/var/repos}"
SINCE="${MERIDIAN_INGEST_SINCE:-24 hours ago}"
LOG_DIR="${LOG_DIR:-/var/log/meridian-deploy}"
TODAY=$(date -u +%Y-%m-%d)
LOG_FILE="${LOG_DIR}/git-ingest-${TODAY}.log"
INGEST_SCRIPT="${INGEST_SCRIPT:-/meridian/scripts/ingest-git-history.py}"

mkdir -p "$LOG_DIR"

{
    echo "=== $(date -u +%FT%TZ) git ingest run ==="
    echo "repo_root=$REPO_ROOT  since='$SINCE'"

    if [ ! -d "$REPO_ROOT" ]; then
        echo "ERROR: $REPO_ROOT does not exist — clone repos there first"
        exit 1
    fi

    fetch_failures=0
    for d in "$REPO_ROOT"/*/; do
        [ -d "$d/.git" ] || continue
        repo=$(basename "$d")
        if git -C "$d" fetch --all --prune --quiet 2>/dev/null; then
            head_branch=$(git -C "$d" rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')
            # Fast-forward the checked-out branch so iter_commits sees the latest
            git -C "$d" pull --ff-only --quiet 2>/dev/null || true
            echo "[$repo] fetched (branch=$head_branch)"
        else
            echo "[$repo] FETCH FAILED"
            fetch_failures=$((fetch_failures + 1))
        fi
    done

    echo "--- running ingest-git-history.py ---"
    MERIDIAN_REPO_ROOT="$REPO_ROOT" python3 "$INGEST_SCRIPT" --since "$SINCE" --no-stats
    ingest_exit=$?
    if [ $ingest_exit -ne 0 ]; then
        echo "WARNING: ingest exited $ingest_exit"
    fi

    echo "=== $(date -u +%FT%TZ) done (fetch_failures=$fetch_failures) ==="
} >> "$LOG_FILE" 2>&1

find "$LOG_DIR" -type f -name 'git-ingest-*.log' -mtime +28 -delete 2>/dev/null || true
