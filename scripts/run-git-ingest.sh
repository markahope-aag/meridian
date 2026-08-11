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
TARGET_FQDN="${MERIDIAN_RECEIVER_FQDN:-meridian.markahope.com}"
COMMITS_DIR="${MERIDIAN_COMMITS_DIR:-/meridian/capture/external/commits}"
ENGINEERING_DIR="${MERIDIAN_ENGINEERING_DIR:-/meridian/wiki/engineering}"
JOBS_DB="${MERIDIAN_JOBS_DB:-/meridian/state/jobs.db}"
CLASSIFY_LOCK="${MERIDIAN_CLASSIFY_LOCK:-/tmp/meridian-classify.lock}"

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

# Hand the ingested fragments to the container user.
#
# This cron runs on the host as root, so every fragment it writes is
# root-owned. The classifier runs inside the receiver container as a
# different user and has to rewrite each fragment's frontmatter and then
# move it into wiki/engineering/. It could not: every write failed with
# EACCES while still counting as "classified", so runs reported
# "Classified 9, Errors 10" and moved nothing.
#
# Re-chowning the whole tree (not just this run's new files) means the
# next scheduled ingest repairs the entire existing backlog on its own.
handoff_ownership() {
    local cid uid gid owner
    # Prefer the ownership of a file the receiver process wrote itself.
    # `docker exec id -u` reports the image's USER, which is not
    # necessarily the uid gunicorn workers end up running as, and the
    # classifier subprocess inherits the worker's uid. The job store is
    # created by the receiver at runtime, so its owner is authoritative.
    if [ -f "$JOBS_DB" ]; then
        owner=$(stat -c '%u:%g' "$JOBS_DB" 2>/dev/null)
    fi

    if [ -z "${owner:-}" ]; then
        cid=$(find_container_by_fqdn "$TARGET_FQDN")
        if [ -z "$cid" ]; then
            echo "WARNING: no receiver container found; skipping ownership handoff"
            return
        fi
        uid=$(docker exec "$cid" id -u 2>/dev/null)
        gid=$(docker exec "$cid" id -g 2>/dev/null)
        if [ -z "$uid" ] || [ -z "$gid" ]; then
            echo "WARNING: could not read container uid/gid; skipping ownership handoff"
            return
        fi
        owner="${uid}:${gid}"
        echo "using container uid/gid $owner (job store not found at $JOBS_DB)"
    else
        echo "using job store owner $owner"
    fi

    uid="${owner%%:*}"
    gid="${owner##*:}"
    for d in "$COMMITS_DIR" "$ENGINEERING_DIR"; do
        [ -d "$d" ] || continue
        if chown -R "$uid:$gid" "$d" 2>/dev/null; then
            echo "chown -R $uid:$gid $d"
        else
            echo "WARNING: chown failed on $d"
        fi
    done
}

# Classify what was just ingested.
#
# The consumer runs here, in the producer's own cron, rather than as a
# separate scheduled job. This system has been bitten twice by a producer
# whose consumer was never scheduled: commit fragments piled up to ~2,900
# over four months, and evolution-queued re-syntheses sat pending
# forever. Keeping the pair in one place means there is no second crontab
# entry to forget.
#
# Set MERIDIAN_INGEST_CLASSIFY=0 to run ingestion alone.
classify_new_fragments() {
    local cid rc
    if [ "${MERIDIAN_INGEST_CLASSIFY:-1}" != "1" ]; then
        echo "classification disabled (MERIDIAN_INGEST_CLASSIFY=0)"
        return
    fi
    cid=$(find_container_by_fqdn "$TARGET_FQDN")
    if [ -z "$cid" ]; then
        echo "WARNING: no receiver container found; skipping classification"
        return
    fi
    # Share a lock with run-classify-engineering.sh so a manual backfill
    # and this run can never move the same fragment at once.
    (
        flock -n 8 || { echo "another classify run is in progress, skipping"; exit 0; }
        docker exec "$cid" python3 /meridian/scripts/classify-engineering-fragments.py \
            --limit "${MERIDIAN_CLASSIFY_LIMIT:-250}"
    ) 8>"$CLASSIFY_LOCK"
    rc=$?
    if [ $rc -ne 0 ]; then
        echo "WARNING: classifier exited $rc"
    fi
}

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

    echo "--- ownership handoff to the receiver container ---"
    handoff_ownership

    echo "--- classifying newly ingested fragments ---"
    classify_new_fragments

    echo "=== $(date -u +%FT%TZ) done (fetch_failures=$fetch_failures) ==="
} >> "$LOG_FILE" 2>&1

find "$LOG_DIR" -type f -name 'git-ingest-*.log' -mtime +28 -delete 2>/dev/null || true
