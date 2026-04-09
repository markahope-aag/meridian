#!/bin/bash
# Meridian — restore from Restic snapshot
#
# Two modes:
#   1. Dry-run (default): list snapshots, show what's in the latest one.
#   2. Restore (--confirm): download a snapshot into --target.
#
# Usage:
#   bash scripts/restore-restic.sh                                   # list + dry-run
#   bash scripts/restore-restic.sh --confirm                         # restore latest → /tmp/...
#   bash scripts/restore-restic.sh --confirm --snapshot <id>         # restore specific snapshot
#   bash scripts/restore-restic.sh --confirm --target /meridian.new  # restore to a specific dir
#
# For disaster recovery: restore to a staging dir, verify, then swap into place.

set -euo pipefail

ENV_FILE="/root/.meridian-backup.env"
DEFAULT_TARGET="/tmp/meridian-restore-$(date -u +%Y%m%dT%H%M%SZ)"

SNAPSHOT="latest"
TARGET="$DEFAULT_TARGET"
CONFIRM=0

while [ $# -gt 0 ]; do
    case "$1" in
        --confirm) CONFIRM=1; shift ;;
        --snapshot) SNAPSHOT="$2"; shift 2 ;;
        --target)   TARGET="$2"; shift 2 ;;
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

: "${RESTIC_REPOSITORY:?RESTIC_REPOSITORY is required}"
: "${RESTIC_PASSWORD:?RESTIC_PASSWORD is required}"
: "${AWS_ACCESS_KEY_ID:?AWS_ACCESS_KEY_ID is required}"
: "${AWS_SECRET_ACCESS_KEY:?AWS_SECRET_ACCESS_KEY is required}"
export RESTIC_REPOSITORY RESTIC_PASSWORD AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY

echo "== Meridian restic restore =="
echo "repo:     $RESTIC_REPOSITORY"
echo "snapshot: $SNAPSHOT"
echo "target:   $TARGET"
echo

echo "-- Available snapshots --"
restic snapshots --compact
echo

if [ "$CONFIRM" -eq 0 ]; then
    echo "-- Latest snapshot contents (top level) --"
    restic ls "$SNAPSHOT" --long 2>&1 | head -20
    echo
    echo "Re-run with --confirm to actually restore."
    exit 0
fi

mkdir -p "$TARGET"
restic restore "$SNAPSHOT" --target "$TARGET"

echo
echo "Restore complete → $TARGET"
echo "The restored tree lives under \$TARGET/meridian/ (restic preserves absolute paths)."
echo "Inspect, then — if you're swapping it in — stop the receiver, move /meridian aside,"
echo "and mv $TARGET/meridian to /meridian."
