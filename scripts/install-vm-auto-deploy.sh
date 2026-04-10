#!/bin/bash
# One-time bootstrap: turn /meridian into a proper git checkout of
# markahope-aag/meridian and install the auto-deploy cron.
#
# Run this on the VM once, from an arbitrary CWD (does not need to be
# inside /meridian). Safe to re-run — idempotent.
#
# Preconditions (enforced):
#   - /meridian exists and is writable
#   - The intended .gitignore has been merged to origin/<BRANCH> already
#   - A fresh restic snapshot was taken right before running this

set -euo pipefail

REPO_DIR="${MERIDIAN_REPO_DIR:-/meridian}"
BRANCH="${MERIDIAN_BRANCH:-april-2026-rebuild}"
REMOTE_URL="${MERIDIAN_REPO_URL:-https://github.com/markahope-aag/meridian.git}"

if [ ! -d "$REPO_DIR" ]; then
    echo "FATAL: $REPO_DIR does not exist" >&2
    exit 2
fi

cd "$REPO_DIR"

echo "== Meridian VM auto-deploy bootstrap =="
echo "dir:    $REPO_DIR"
echo "branch: $BRANCH"
echo "remote: $REMOTE_URL"
echo

if [ ! -d .git ]; then
    echo "[1/5] initializing git repository"
    git init --quiet --initial-branch="$BRANCH"
else
    echo "[1/5] .git already present — skipping git init"
fi

# Ensure remote is set correctly.
if git remote get-url origin >/dev/null 2>&1; then
    CURRENT_URL=$(git remote get-url origin)
    if [ "$CURRENT_URL" != "$REMOTE_URL" ]; then
        echo "[2/5] updating origin URL ($CURRENT_URL → $REMOTE_URL)"
        git remote set-url origin "$REMOTE_URL"
    else
        echo "[2/5] origin already set to $REMOTE_URL"
    fi
else
    echo "[2/5] adding origin $REMOTE_URL"
    git remote add origin "$REMOTE_URL"
fi

echo "[3/5] fetching $BRANCH from origin"
git fetch --quiet origin "$BRANCH"

echo "[4/5] aligning working tree to origin/$BRANCH"
# The remote .gitignore is already right (we prepared it before
# running this). `git reset --hard` only touches tracked files; content
# directories listed in .gitignore are left alone.
git reset --hard "origin/$BRANCH"
git branch --set-upstream-to="origin/$BRANCH" "$BRANCH" 2>/dev/null \
    || git checkout -B "$BRANCH" "origin/$BRANCH"

echo "[5/5] installing auto-deploy cron"
# Drop the env file so cron knows which branch/dir/etc.
ENV_FILE="/etc/meridian-deploy.env"
if [ ! -f "$ENV_FILE" ]; then
    cat > "$ENV_FILE" <<EOF
MERIDIAN_REPO_DIR=$REPO_DIR
MERIDIAN_BRANCH=$BRANCH
EOF
    chmod 644 "$ENV_FILE"
    echo "  wrote $ENV_FILE"
fi

# Install cron line only if not already present.
CRON_LINE="* * * * *  /bin/bash $REPO_DIR/scripts/vm-auto-deploy.sh"
if ! crontab -l 2>/dev/null | grep -Fq 'vm-auto-deploy.sh'; then
    ( crontab -l 2>/dev/null || true; echo "$CRON_LINE" ) | crontab -
    echo "  installed crontab entry"
else
    echo "  crontab entry already present"
fi

echo
echo "== Bootstrap complete =="
echo "Current HEAD:    $(git rev-parse --short HEAD)"
echo "Tracking:        origin/$BRANCH"
echo "Cron:            every minute via /etc/cron.d or the user crontab"
echo
echo "Smoke test: make a trivial commit on origin, wait up to 60s, check:"
echo "  tail -f /var/log/meridian-deploy/deploy-\$(date -u +%Y-%m-%d).log"
