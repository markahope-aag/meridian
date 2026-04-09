#!/bin/bash
# Meridian — restore from R2 backup
#
# Used for both disaster recovery and the quarterly restore drill. Restores
# to a target directory (default: /tmp/meridian-restore-<timestamp>) so you
# can inspect before swapping into /meridian.
#
# Usage:
#   bash scripts/restore-from-r2.sh                  # dry-run listing
#   bash scripts/restore-from-r2.sh --confirm        # actually restore
#   bash scripts/restore-from-r2.sh --confirm --target /meridian.restore

set -euo pipefail

ENV_FILE="/root/.meridian-backup.env"
DEFAULT_TARGET="/tmp/meridian-restore-$(date -u +%Y%m%dT%H%M%SZ)"

TARGET="$DEFAULT_TARGET"
CONFIRM=0

while [ $# -gt 0 ]; do
    case "$1" in
        --confirm) CONFIRM=1; shift ;;
        --target) TARGET="$2"; shift 2 ;;
        -h|--help)
            grep '^#' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "unknown arg: $1" >&2
            exit 1
            ;;
    esac
done

if [ ! -r "$ENV_FILE" ]; then
    echo "FATAL: missing $ENV_FILE" >&2
    exit 2
fi
# shellcheck disable=SC1090
source "$ENV_FILE"

BUCKET="${MERIDIAN_BACKUP_BUCKET:?MERIDIAN_BACKUP_BUCKET is required}"
REMOTE="${MERIDIAN_BACKUP_REMOTE:-meridian-r2}"
SRC="${REMOTE}:${BUCKET}"

echo "== Meridian restore =="
echo "source: $SRC"
echo "target: $TARGET"

echo
echo "-- Bucket status --"
rclone cat "$SRC/_backup-status.yaml" 2>/dev/null || echo "(no _backup-status.yaml — has a backup ever run?)"
echo

if [ "$CONFIRM" -eq 0 ]; then
    echo "-- Dry-run (top-level listing) --"
    rclone lsd "$SRC"
    echo
    echo "Re-run with --confirm to actually download."
    exit 0
fi

mkdir -p "$TARGET"
rclone copy "$SRC" "$TARGET" \
    --transfers 8 \
    --checkers 16 \
    --fast-list \
    --stats 1m \
    --stats-one-line \
    --log-level INFO

echo
echo "Restore complete → $TARGET"
echo "Inspect, then (if you're overwriting the live tree) stop the receiver,"
echo "move /meridian aside, and swap in the restored copy."
