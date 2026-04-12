#!/bin/bash
# Weekly wrapper for agents/linter.py. Runs every Sunday at 06:00 UTC
# via the VM crontab, one hour before the evolution detector.
#
# Uses docker exec (same as the evolution detector wrapper) instead of
# the n8n fire-and-forget HTTP approach. docker exec runs the Python
# process directly in the container — no HTTP intermediary, no
# background threading, no SIGHUP race. The process runs to completion
# or crashes with an exit code the wrapper captures.
#
# After execution, checks that the report file was actually created
# and logs an error if it wasn't — solving the "lint ran but produced
# no output and nobody noticed" failure mode from the n8n approach.
#
# Logs to /var/log/meridian-deploy/lint-<date>.log.

set -uo pipefail

LOG_DIR="${LOG_DIR:-/var/log/meridian-deploy}"
TODAY=$(date -u +%Y-%m-%d)
LOG_FILE="${LOG_DIR}/lint-${TODAY}.log"
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
    echo "=== $(date -u +%FT%TZ) weekly lint run ==="
    cid=$(find_container_by_fqdn "$TARGET_FQDN")
    if [ -z "$cid" ]; then
        echo "ERROR: no container found matching FQDN '$TARGET_FQDN'"
        exit 1
    fi
    echo "target container: $cid ($TARGET_FQDN)"

    # Run the linter (full scope, auto-fix enabled)
    docker exec "$cid" python3 /meridian/agents/linter.py --scope all
    exit_code=$?

    if [ $exit_code -ne 0 ]; then
        echo "ERROR: linter exited with code $exit_code"
    fi

    # Verify report files were created
    report_ok=0
    for path in "/meridian/reports/lint/lint-${TODAY}.md" "/meridian/wiki/articles/lint-${TODAY}.md"; do
        if docker exec "$cid" test -f "$path" 2>/dev/null; then
            size=$(docker exec "$cid" wc -c < "$path" 2>/dev/null)
            echo "report OK: $path ($size bytes)"
            report_ok=1
        fi
    done

    if [ $report_ok -eq 0 ]; then
        echo "WARNING: no lint report file found for $TODAY — linter may have crashed during LLM analysis"
    fi

    echo "=== $(date -u +%FT%TZ) weekly lint finished ==="
} >> "$LOG_FILE" 2>&1

# Keep logs bounded (28-day retention)
find "$LOG_DIR" -type f -name 'lint-*.log' -mtime +28 -delete 2>/dev/null || true
