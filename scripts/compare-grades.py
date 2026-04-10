#!/usr/bin/env python3
"""Compare two directories of synthesis grades side-by-side.

Useful when A/B testing a prompt change:
  1. Save baseline grades: scripts/grade-synthesis.py --batch baseline --out grades/before
  2. Change the prompt, run the harness, grade again into grades/after
  3. scripts/compare-grades.py grades/before grades/after

Prints a per-article, per-criterion delta table and a summary of which
criteria improved or regressed on average.

Usage:
    python scripts/compare-grades.py <before-dir> <after-dir>
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path


def _load(grades_dir: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not grades_dir.is_dir():
        print(f"Not a directory: {grades_dir}", file=sys.stderr)
        sys.exit(2)
    for f in sorted(grades_dir.glob("*.json")):
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"  skip unreadable {f}", file=sys.stderr)
            continue
        out[f.stem] = rec
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare two grades/ directories")
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument(
        "--criterion",
        help="Only show this criterion (e.g. summary_quality). Default: all.",
    )
    args = parser.parse_args()

    before = _load(args.before)
    after = _load(args.after)

    shared = sorted(set(before) & set(after))
    if not shared:
        print("No overlapping articles between the two directories.", file=sys.stderr)
        sys.exit(1)

    criterion_deltas: dict[str, list[float]] = defaultdict(list)
    article_deltas: list[tuple[str, float, float]] = []

    print(f"{'article':<20}  before  after   delta")
    print("-" * 50)
    for name in shared:
        b = before[name]
        a = after[name]
        b_avg = b.get("average") or 0
        a_avg = a.get("average") or 0
        delta = round(a_avg - b_avg, 2)
        article_deltas.append((name, b_avg, a_avg))
        print(f"{name:<20}  {b_avg:5.2f}  {a_avg:5.2f}  {delta:+5.2f}")

        b_scores = (b.get("scores") or {})
        a_scores = (a.get("scores") or {})
        for criterion in sorted(set(b_scores) | set(a_scores)):
            bs = (b_scores.get(criterion) or {}).get("score")
            as_ = (a_scores.get(criterion) or {}).get("score")
            if isinstance(bs, (int, float)) and isinstance(as_, (int, float)):
                criterion_deltas[criterion].append(as_ - bs)

    # Average movement per criterion.
    print()
    print("per-criterion average delta (across all articles):")
    print("-" * 50)
    rows = []
    for criterion, diffs in criterion_deltas.items():
        avg = sum(diffs) / len(diffs)
        if args.criterion and criterion != args.criterion:
            continue
        rows.append((criterion, avg, len(diffs)))
    rows.sort(key=lambda r: r[1], reverse=True)
    for criterion, avg, n in rows:
        arrow = "↑" if avg > 0.05 else ("↓" if avg < -0.05 else "·")
        print(f"  {arrow} {criterion:<30}  {avg:+5.2f}   (n={n})")

    # Summary line.
    overall = sum(a - b for _, b, a in article_deltas) / len(article_deltas)
    direction = "improvement" if overall > 0 else ("regression" if overall < 0 else "flat")
    print()
    print(f"Overall average delta: {overall:+.2f}  ({direction})")


if __name__ == "__main__":
    main()
