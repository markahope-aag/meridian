#!/bin/bash
# Show per-topic diffs between a baseline and the most recent test run.
#
# Usage:
#   bash scripts/diff-synthesis.sh 2026-04-10
#   bash scripts/diff-synthesis.sh 2026-04-10 website hubspot   # only these topics
#
# If no baseline date is given, the most recent directory under
# tests/synthesis_corpus/baselines/ is used.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CORPUS_DIR="${CORPUS_DIR:-$ROOT/tests/synthesis_corpus}"
LATEST_DIR="$CORPUS_DIR/latest"

if [ ! -d "$LATEST_DIR" ]; then
    echo "No latest run found at $LATEST_DIR" >&2
    echo "Run: bash scripts/test-synthesis.sh" >&2
    exit 2
fi

if [ $# -ge 1 ] && [ -d "$CORPUS_DIR/baselines/$1" ]; then
    BASE_DATE="$1"
    shift
else
    BASE_DATE="$(ls -1 "$CORPUS_DIR/baselines" 2>/dev/null | sort | tail -1 || true)"
    if [ -z "$BASE_DATE" ]; then
        echo "No baselines found under $CORPUS_DIR/baselines/" >&2
        exit 2
    fi
    echo "Using baseline: $BASE_DATE (pass a date as first arg to pick another)"
fi

BASE_DIR="$CORPUS_DIR/baselines/$BASE_DATE"
echo "Comparing $BASE_DIR  vs  $LATEST_DIR"
echo

if [ $# -gt 0 ]; then
    TOPICS=("$@")
else
    TOPICS=()
    for f in "$LATEST_DIR"/*.md; do
        [ -f "$f" ] || continue
        TOPICS+=("$(basename "$f" .md)")
    done
fi

changed=0
same=0
missing=0
for topic in "${TOPICS[@]}"; do
    base="$BASE_DIR/$topic.md"
    latest="$LATEST_DIR/$topic.md"
    if [ ! -f "$latest" ]; then
        echo "  SKIP $topic — no latest output"
        missing=$((missing + 1))
        continue
    fi
    if [ ! -f "$base" ]; then
        echo "  NEW  $topic — no baseline"
        continue
    fi
    # Strip the generated_at / run_id / prompt_sha lines so we only diff
    # the actual content; those fields change on every run by design.
    strip='^\(generated_at\|run_id\|synthesizer_prompt_sha\|extract_prompt_sha\|writer_model\|extract_model\|extraction_cache_hit\|last_updated\):'
    if diff -q <(grep -v "$strip" "$base") <(grep -v "$strip" "$latest") >/dev/null 2>&1; then
        echo "  ==   $topic"
        same=$((same + 1))
    else
        echo "  DIFF $topic"
        changed=$((changed + 1))
        diff -u \
            --label "baseline/$BASE_DATE/$topic.md" \
            --label "latest/$topic.md" \
            <(grep -v "$strip" "$base") \
            <(grep -v "$strip" "$latest") \
            | head -200
        echo
    fi
done

echo
echo "Summary: ${changed} changed, ${same} unchanged, ${missing} missing"
