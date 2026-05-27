#!/bin/bash
# Sweep stale staging directories left behind by daily distill.
#
# Each distill run creates capture/.processing/<UTC-timestamp>-<id>/
# to atomically claim its batch. On success the contents are moved to
# raw/ but the empty (or near-empty) staging dir is left in place.
# Without this sweep they accumulate forever.
#
# Removes any .processing/<dir> older than RETENTION_DAYS (default 14).
# Runs daily via VM crontab.

set -uo pipefail

PROCESSING_DIR="${MERIDIAN_PROCESSING_DIR:-/meridian/capture/.processing}"
RETENTION_DAYS="${MERIDIAN_PROCESSING_RETENTION_DAYS:-14}"
LOG_DIR="${LOG_DIR:-/var/log/meridian-deploy}"
TODAY=$(date -u +%Y-%m-%d)
LOG_FILE="${LOG_DIR}/sweep-processing-${TODAY}.log"

mkdir -p "$LOG_DIR"

{
    echo "=== $(date -u +%FT%TZ) processing sweep ==="
    if [ ! -d "$PROCESSING_DIR" ]; then
        echo "no processing dir at $PROCESSING_DIR — nothing to do"
        exit 0
    fi

    before=$(find "$PROCESSING_DIR" -mindepth 1 -maxdepth 1 -type d | wc -l)
    deleted=0
    while IFS= read -r -d '' dir; do
        echo "delete: $dir"
        rm -rf -- "$dir" && deleted=$((deleted + 1))
    done < <(find "$PROCESSING_DIR" -mindepth 1 -maxdepth 1 -type d -mtime "+${RETENTION_DAYS}" -print0)

    after=$(find "$PROCESSING_DIR" -mindepth 1 -maxdepth 1 -type d | wc -l)
    echo "before=$before deleted=$deleted after=$after retention_days=$RETENTION_DAYS"
    echo "=== $(date -u +%FT%TZ) done ==="
} >> "$LOG_FILE" 2>&1

find "$LOG_DIR" -type f -name 'sweep-processing-*.log' -mtime +28 -delete 2>/dev/null || true
