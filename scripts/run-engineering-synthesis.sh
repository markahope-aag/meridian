#!/bin/bash
# Daily wrapper to keep wiki/engineering/ Layer 3 articles fresh as new
# commit fragments flow in. Picks the N engineering topics with the
# oldest (or missing) `generated_at` and re-synthesizes them via
# agents/synthesizer.py.
#
# Why this exists: synthesis_scheduler.py only processes the knowledge
# (topic) dimension. Engineering articles would never re-synthesize
# without manual intervention.
#
# Scheduled daily at 04:30 UTC (30 min after the knowledge synthesis).
#
# Configuration:
#   MERIDIAN_ENGINEERING_LIMIT   topics per run (default 3)
#   MERIDIAN_RECEIVER_FQDN       discovery FQDN (default meridian.markahope.com)

set -uo pipefail

LIMIT="${MERIDIAN_ENGINEERING_LIMIT:-3}"
TARGET_FQDN="${MERIDIAN_RECEIVER_FQDN:-meridian.markahope.com}"
LOG_DIR="${LOG_DIR:-/var/log/meridian-deploy}"
TODAY=$(date -u +%Y-%m-%d)
LOG_FILE="${LOG_DIR}/engineering-synth-${TODAY}.log"
WIKI_ENG=/meridian/wiki/engineering

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

# Emit "<generated_at>\t<slug>" for every engineering topic that has at
# least one Layer 2 fragment. Missing generated_at sorts first (0).
list_topics_by_staleness() {
    for d in "$WIKI_ENG"/*/; do
        slug=$(basename "$d")
        [ "$slug" = "unclassified" ] && continue
        frag_count=$(find "$d" -maxdepth 1 -name '*.md' ! -name 'index.md' 2>/dev/null | wc -l)
        [ "$frag_count" -eq 0 ] && continue
        gen=""
        if [ -f "$d/index.md" ]; then
            gen=$(grep -E '^generated_at:' "$d/index.md" 2>/dev/null | head -1 | sed 's/generated_at:[" ]*//;s/[" ]*$//')
        fi
        [ -z "$gen" ] && gen="0000-00-00T00:00:00Z"
        echo -e "${gen}\t${slug}"
    done | sort
}

{
    echo "=== $(date -u +%FT%TZ) engineering synthesis run (limit=$LIMIT) ==="
    cid=$(find_container_by_fqdn "$TARGET_FQDN")
    if [ -z "$cid" ]; then
        echo "ERROR: no container found matching FQDN '$TARGET_FQDN'"
        exit 1
    fi
    echo "target container: $cid"

    picked=$(list_topics_by_staleness | head -n "$LIMIT" | cut -f2)
    if [ -z "$picked" ]; then
        echo "no engineering topics with fragments to synthesize"
        exit 0
    fi
    echo "rotating topics:"
    echo "$picked" | sed 's/^/  - /'

    for slug in $picked; do
        echo "--- synthesizing $slug ---"
        docker exec "$cid" python3 /meridian/agents/synthesizer.py \
            --topic "$slug" --dimension engineering --force 2>&1 \
            | tail -5
    done

    echo "=== $(date -u +%FT%TZ) finished ==="
} >> "$LOG_FILE" 2>&1

find "$LOG_DIR" -type f -name 'engineering-synth-*.log' -mtime +28 -delete 2>/dev/null || true
