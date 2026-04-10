#!/bin/bash
# Meridian VM auto-deploy — pull the tracked branch and reload gunicorn
# when code actually changed.
#
# Runs every minute via cron on the Hetzner VM. Idempotent: if nothing
# new is on origin, exits quietly. If git pull updated anything, checks
# whether the files that matter to the live receiver were touched and
# triggers a gunicorn SIGHUP.
#
# Installed by scripts/install-vm-auto-deploy.sh.
#
# Configuration (via env or /etc/meridian-deploy.env):
#   MERIDIAN_REPO_DIR       default: /meridian
#   MERIDIAN_BRANCH         default: april-2026-rebuild
#   MERIDIAN_RECEIVER_CID   default: auto-discovered by image name
#   MERIDIAN_DASHBOARD_CID  default: auto-discovered by image name
#   LOG_DIR                 default: /var/log/meridian-deploy

set -euo pipefail

ENV_FILE="/etc/meridian-deploy.env"
if [ -r "$ENV_FILE" ]; then
    # shellcheck disable=SC1090
    source "$ENV_FILE"
fi

REPO_DIR="${MERIDIAN_REPO_DIR:-/meridian}"
BRANCH="${MERIDIAN_BRANCH:-april-2026-rebuild}"
LOG_DIR="${LOG_DIR:-/var/log/meridian-deploy}"
LOG_FILE="${LOG_DIR}/deploy-$(date -u +%Y-%m-%d).log"

# Runtime-mutable scaffolding files that MUST survive every deploy.
# These live at well-known paths inside tracked directories but are
# rewritten by agents at runtime. The bootstrap of this deploy system
# clobbered wiki/log.md once and we don't want that ever again.
# Paths are relative to $REPO_DIR. Add new entries whenever an agent
# starts writing to a file that used to be a seed in git.
CHECKPOINT_FILES=(
    "wiki/log.md"
    "wiki/_index.md"
    "wiki/_backlinks.md"
    "raw/_index.md"
)

mkdir -p "$LOG_DIR"
cd "$REPO_DIR"

# Prevent concurrent pulls. Cron fires every minute; a slow pull on a
# flaky network could otherwise stack up.
LOCK_FILE="/tmp/meridian-auto-deploy.lock"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    echo "$(date -u +%FT%TZ) another deploy is already in progress, skipping" >> "$LOG_FILE"
    exit 0
fi

# Only take action when origin actually has new commits on the tracked
# branch. This is the fast path — no network work besides ls-remote.
REMOTE_SHA=$(git ls-remote origin "refs/heads/${BRANCH}" 2>/dev/null | awk '{print $1}')
if [ -z "$REMOTE_SHA" ]; then
    echo "$(date -u +%FT%TZ) ls-remote failed for origin/${BRANCH}" >> "$LOG_FILE"
    exit 0
fi
LOCAL_SHA=$(git rev-parse HEAD)
if [ "$REMOTE_SHA" = "$LOCAL_SHA" ]; then
    # Nothing to do. No log spam on the idle path.
    exit 0
fi

{
    echo "=== $(date -u +%FT%TZ) deploy triggered ==="
    echo "before: $LOCAL_SHA"
    echo "after:  $REMOTE_SHA"

    git fetch --quiet origin "$BRANCH"

    # Record what changed between old and new HEAD so we can decide
    # whether a gunicorn reload is warranted.
    CHANGED_FILES=$(git diff --name-only "$LOCAL_SHA" "$REMOTE_SHA" || true)
    echo "changed files:"
    printf '%s\n' "$CHANGED_FILES" | sed 's/^/  /'

    # Checkpoint runtime-mutable scaffolding so `git reset --hard`
    # can't delete or overwrite files that agents are actively
    # writing to. This is what actually kept log.md safe during the
    # deploy loop — the naive reset would otherwise wipe every line
    # the pipeline has appended since the last commit.
    CHECKPOINT_DIR="$(mktemp -d)"
    trap 'rm -rf "$CHECKPOINT_DIR"' EXIT
    for rel in "${CHECKPOINT_FILES[@]}"; do
        src="$REPO_DIR/$rel"
        if [ -f "$src" ]; then
            mkdir -p "$CHECKPOINT_DIR/$(dirname "$rel")"
            cp -p "$src" "$CHECKPOINT_DIR/$rel"
            echo "  checkpointed $rel ($(wc -l < "$src") lines)"
        fi
    done

    # Fast-forward to the remote HEAD. Hard reset so local scp fiddling
    # can never block a deploy — git is authoritative for tracked
    # code, and the checkpoint above protects runtime scaffolding.
    git reset --hard "origin/$BRANCH"

    # Restore checkpointed files. This is a no-op for files that are
    # still tracked at the new HEAD (git reset just restored them to
    # their repo content), unless the repo content is older than
    # what the agents wrote — in which case the checkpoint wins.
    for rel in "${CHECKPOINT_FILES[@]}"; do
        ckpt="$CHECKPOINT_DIR/$rel"
        dst="$REPO_DIR/$rel"
        if [ -f "$ckpt" ]; then
            mkdir -p "$(dirname "$dst")"
            cp -p "$ckpt" "$dst"
            # Preserve the ownership the agents expect
            chown --reference="$REPO_DIR/agents" "$dst" 2>/dev/null || true
            echo "  restored $rel"
        fi
    done

    # Decide which containers (if any) need a gunicorn reload.
    needs_receiver_reload=0
    needs_dashboard_reload=0
    while IFS= read -r f; do
        case "$f" in
            receiver/*|agents/*|prompts/*|config.yaml|clients.yaml|topics.yaml)
                needs_receiver_reload=1
                ;;
            web/*)
                needs_dashboard_reload=1
                ;;
        esac
    done <<< "$CHANGED_FILES"

    # Receiver container: identify by the path its gunicorn master was
    # launched with (`--chdir /meridian/receiver`). Works regardless of
    # Coolify's hashed container name.
    reload_container() {
        local label="$1"
        local match="$2"
        local cid
        cid=$(docker ps --format '{{.ID}} {{.Command}}' 2>/dev/null \
            | awk -v m="$match" 'index($0, m) { print $1; exit }')
        if [ -z "$cid" ]; then
            echo "  (no $label container found — skipping reload)"
            return
        fi
        echo "  reloading $label container $cid"
        docker exec "$cid" sh -c 'kill -HUP 1' 2>/dev/null || echo "    HUP failed"
    }

    if [ "$needs_receiver_reload" -eq 1 ]; then
        reload_container receiver '/meridian/receiver'
    fi
    if [ "$needs_dashboard_reload" -eq 1 ]; then
        reload_container dashboard 'app:app'
    fi

    echo "=== $(date -u +%FT%TZ) deploy finished ==="
} >> "$LOG_FILE" 2>&1

# Keep logs bounded.
find "$LOG_DIR" -type f -name 'deploy-*.log' -mtime +14 -delete 2>/dev/null || true
