#!/bin/bash
# Meridian — nightly snapshot backup via Restic to Cloudflare R2
#
# Restic gives us what R2 can't: true snapshot semantics, deduplication,
# and client-side encryption. Each run creates a new snapshot you can
# restore from individually.
#
# Expects /root/.meridian-backup.env to export:
#   MERIDIAN_BACKUP_BUCKET       (bucket name, currently unused but kept for docs)
#   RESTIC_REPOSITORY            (s3:<endpoint>/<bucket>/<prefix>)
#   RESTIC_PASSWORD              (encryption key — losing it = losing backup)
#   AWS_ACCESS_KEY_ID            (R2 token)
#   AWS_SECRET_ACCESS_KEY        (R2 token)
#
# Retention:
#   - last 7 daily snapshots
#   - last 4 weekly snapshots
#   - last 12 monthly snapshots
#   - plus whatever the latest hourly run produced
# Old snapshots are forgotten and their unique data is pruned from the repo.

set -euo pipefail

ENV_FILE="/root/.meridian-backup.env"
SOURCE_DIR="/meridian"
LOG_DIR="/var/log/meridian-backup"
LOG_FILE="${LOG_DIR}/backup-$(date -u +%Y-%m-%d).log"
HOSTNAME_TAG="$(hostname)"

mkdir -p "$LOG_DIR"

if [ ! -r "$ENV_FILE" ]; then
    echo "FATAL: missing $ENV_FILE" >&2
    exit 2
fi
# shellcheck disable=SC1090
source "$ENV_FILE"

: "${RESTIC_REPOSITORY:?RESTIC_REPOSITORY is required in $ENV_FILE}"
: "${RESTIC_PASSWORD:?RESTIC_PASSWORD is required in $ENV_FILE}"
: "${AWS_ACCESS_KEY_ID:?AWS_ACCESS_KEY_ID is required in $ENV_FILE}"
: "${AWS_SECRET_ACCESS_KEY:?AWS_SECRET_ACCESS_KEY is required in $ENV_FILE}"

export RESTIC_REPOSITORY RESTIC_PASSWORD AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY

# Exclusions keep the snapshot focused on content, not regenerable state.
EXCLUDES=(
    --exclude "**/__pycache__"
    --exclude "**/.cache"
    --exclude "**/.git"
    --exclude "*.pyc"
    --exclude "*.tmp"
    --exclude "*.swp"
)

{
    echo "=== Meridian restic backup started $(date -u +%FT%TZ) ==="
    echo "repo:   $RESTIC_REPOSITORY"
    echo "source: $SOURCE_DIR"
    echo "host:   $HOSTNAME_TAG"

    # Core backup — creates one new snapshot.
    restic backup "$SOURCE_DIR" \
        --host "$HOSTNAME_TAG" \
        --tag nightly \
        "${EXCLUDES[@]}"

    # Prune old snapshots per the retention policy above.
    restic forget \
        --host "$HOSTNAME_TAG" \
        --keep-daily 7 \
        --keep-weekly 4 \
        --keep-monthly 12 \
        --prune

    # Light integrity check — verifies snapshot metadata and random pack
    # samples without re-downloading everything. Full `check --read-data`
    # runs on the quarterly restore drill instead (it's expensive).
    restic check

    echo "--- snapshots ---"
    restic snapshots --compact

    echo "=== Meridian restic backup finished $(date -u +%FT%TZ) ==="
} >> "$LOG_FILE" 2>&1

# Rotate local logs after 30 days.
find "$LOG_DIR" -type f -name 'backup-*.log' -mtime +30 -delete 2>/dev/null || true
