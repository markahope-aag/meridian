#!/bin/bash
# Weekly wrapper for agents/evolution_detector.py. Runs every Sunday
# at 08:00 UTC via the VM crontab, after the Saturday linter.
#
# Discovers the Meridian receiver container by COOLIFY_FQDN (same
# pattern as scripts/vm-auto-deploy.sh) rather than hardcoding a
# container ID — a Coolify rebuild changes the ID, and a cron
# with a stale ID just silently stops running.
#
# Logs to /var/log/meridian-deploy/evolution-<date>.log so retention
# lines up with the existing deploy logs.

set -uo pipefail

LOG_DIR="${LOG_DIR:-/var/log/meridian-deploy}"
LOG_FILE="${LOG_DIR}/evolution-$(date -u +%Y-%m-%d).log"
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
    echo "=== $(date -u +%FT%TZ) evolution detector run ==="
    cid=$(find_container_by_fqdn "$TARGET_FQDN")
    if [ -z "$cid" ]; then
        echo "ERROR: no container found matching FQDN '$TARGET_FQDN'"
        exit 1
    fi
    echo "target container: $cid ($TARGET_FQDN)"
    docker exec "$cid" python3 /meridian/agents/evolution_detector.py

    # Drain the drift queue this run just filled.
    #
    # The detector queues re-syntheses tagged queued_by=evolution_detector,
    # and synthesis_scheduler.py deliberately excludes tagged items from
    # its default daily run because a separate job was meant to claim
    # them. No such job existed, so every detection since the auto-queue
    # landed sat pending forever. Draining here keeps the producer and
    # consumer in one place.
    #
    # Set MERIDIAN_DRIFT_DRAIN=0 to detect without re-synthesizing.
    if [ "${MERIDIAN_DRIFT_DRAIN:-1}" = "1" ]; then
        echo "--- draining drift queue ---"
        docker exec "$cid" python3 /meridian/agents/synthesis_scheduler.py \
            --queued-by evolution_detector --limit "${MERIDIAN_DRIFT_LIMIT:-10}"
        drain_exit=$?
        if [ $drain_exit -ne 0 ]; then
            echo "WARNING: drift drain exited $drain_exit"
        fi
    else
        echo "drift drain disabled (MERIDIAN_DRIFT_DRAIN=0)"
    fi

    echo "=== $(date -u +%FT%TZ) evolution detector finished ==="
} >> "$LOG_FILE" 2>&1

# Keep logs bounded (28-day retention)
find "$LOG_DIR" -type f -name 'evolution-*.log' -mtime +28 -delete 2>/dev/null || true
