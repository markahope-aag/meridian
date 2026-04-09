#!/bin/bash
# Meridian — nightly backup to Cloudflare R2
#
# Mirrors /meridian to an R2 bucket using rclone. Designed to run from cron
# on the Hetzner VM, not inside a container.
#
# Expects an rclone remote named "meridian-r2" configured with the
# Cloudflare R2 S3-compatible endpoint. Set it up once with:
#
#   rclone config create meridian-r2 s3 \
#     provider=Cloudflare \
#     access_key_id=<token-id> \
#     secret_access_key=<token-secret> \
#     endpoint=https://<accountid>.r2.cloudflarestorage.com \
#     acl=private
#
# Runtime configuration lives in /root/.meridian-backup.env (owner-only).
# That file must define MERIDIAN_BACKUP_BUCKET (the R2 bucket name).

set -euo pipefail

ENV_FILE="/root/.meridian-backup.env"
SOURCE_DIR="/meridian"
LOG_DIR="/var/log/meridian-backup"
LOG_FILE="${LOG_DIR}/backup-$(date -u +%Y-%m-%d).log"

mkdir -p "$LOG_DIR"

# Load bucket name and any overrides.
if [ ! -r "$ENV_FILE" ]; then
    echo "FATAL: missing $ENV_FILE — copy from scripts/backup-to-r2.env.example and fill in" >&2
    exit 2
fi
# shellcheck disable=SC1090
source "$ENV_FILE"

BUCKET="${MERIDIAN_BACKUP_BUCKET:?MERIDIAN_BACKUP_BUCKET is required in $ENV_FILE}"
REMOTE="${MERIDIAN_BACKUP_REMOTE:-meridian-r2}"
DEST="${REMOTE}:${BUCKET}"

# Exclude ephemeral or regenerable state to keep the backup tight.
# - __pycache__/.git: not content
# - *.log / jobs: transient
# - .cache: rclone/tool caches
EXCLUDES=(
    --exclude "**/__pycache__/**"
    --exclude "**/.git/**"
    --exclude "**/.cache/**"
    --exclude "*.pyc"
    --exclude "*.tmp"
)

{
    echo "=== Meridian backup started $(date -u +%FT%TZ) ==="
    echo "source:  $SOURCE_DIR"
    echo "dest:    $DEST"

    # `sync` is a mirror: anything deleted locally is deleted in the bucket.
    # Combined with R2 object versioning this still preserves history, but
    # without versioning a local deletion becomes permanent. Versioning MUST
    # be enabled on the bucket — see scripts/backup-to-r2.md.
    rclone sync "$SOURCE_DIR" "$DEST" \
        "${EXCLUDES[@]}" \
        --transfers 8 \
        --checkers 16 \
        --fast-list \
        --stats 1m \
        --stats-one-line \
        --log-level INFO

    # Write a marker object so we can tell from the bucket side when the
    # last successful sync happened.
    MARKER="$(mktemp)"
    trap 'rm -f "$MARKER"' EXIT
    printf 'last_backup_utc: %s\nhost: %s\n' "$(date -u +%FT%TZ)" "$(hostname)" > "$MARKER"
    rclone copyto "$MARKER" "$DEST/_backup-status.yaml" --log-level INFO

    echo "=== Meridian backup finished $(date -u +%FT%TZ) ==="
} >> "$LOG_FILE" 2>&1

# Prune local logs older than 30 days.
find "$LOG_DIR" -type f -name 'backup-*.log' -mtime +30 -delete 2>/dev/null || true
