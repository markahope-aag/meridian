#!/bin/bash
# Run the write pass against every frozen fixture in the synthesis corpus.
#
# Outputs land in tests/synthesis_corpus/latest/. Does NOT touch
# production state — no archive, no log, no wiki write. Each fixture
# goes through `synthesizer.py write --fixture … --output …`.
#
# Intended to run on the VM (where anthropic + API key are available)
# or on any workstation with the dependencies installed.
#
# Usage:
#   bash scripts/test-synthesis.sh
#   bash scripts/test-synthesis.sh website hubspot     # subset of topics
#   CORPUS_DIR=/path/to/alt/corpus bash scripts/test-synthesis.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CORPUS_DIR="${CORPUS_DIR:-$ROOT/tests/synthesis_corpus}"
FIXTURES_DIR="$CORPUS_DIR/fixtures"
OUT_DIR="$CORPUS_DIR/latest"
PY="${PY:-python3.11}"

if [ ! -d "$FIXTURES_DIR" ]; then
    echo "Missing fixtures dir: $FIXTURES_DIR" >&2
    exit 2
fi

mkdir -p "$OUT_DIR"

# Build the topic list: either explicit args, or every .json in fixtures/.
if [ $# -gt 0 ]; then
    TOPICS=("$@")
else
    TOPICS=()
    for f in "$FIXTURES_DIR"/*.json; do
        [ -f "$f" ] || continue
        name="$(basename "$f" .json)"
        TOPICS+=("$name")
    done
fi

if [ ${#TOPICS[@]} -eq 0 ]; then
    echo "No fixtures found in $FIXTURES_DIR" >&2
    exit 2
fi

echo "Running ${#TOPICS[@]} fixture(s) from $FIXTURES_DIR"
echo "Writing outputs to $OUT_DIR"
echo

start=$(date +%s)
ok=0
fail=0
for topic in "${TOPICS[@]}"; do
    fixture="$FIXTURES_DIR/$topic.json"
    out="$OUT_DIR/$topic.md"
    if [ ! -f "$fixture" ]; then
        echo "  SKIP $topic — fixture missing: $fixture" >&2
        fail=$((fail + 1))
        continue
    fi
    printf "  %-20s " "$topic"
    t0=$(date +%s)
    if "$PY" "$ROOT/agents/synthesizer.py" write \
            --topic "$topic" \
            --fixture "$fixture" \
            --output "$out" \
            > /tmp/test-synthesis-$$.json 2>/tmp/test-synthesis-$$.err; then
        dt=$(( $(date +%s) - t0 ))
        words=$(wc -w < "$out" 2>/dev/null || echo 0)
        echo "ok   ${dt}s  ${words}w"
        ok=$((ok + 1))
    else
        echo "FAIL"
        tail -5 /tmp/test-synthesis-$$.err | sed 's/^/    /'
        fail=$((fail + 1))
    fi
    rm -f /tmp/test-synthesis-$$.json /tmp/test-synthesis-$$.err
done

elapsed=$(( $(date +%s) - start ))
echo
echo "Done in ${elapsed}s — ${ok} ok, ${fail} fail"
echo "Review with: bash scripts/diff-synthesis.sh <baseline-date>"
