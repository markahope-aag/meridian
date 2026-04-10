#!/usr/bin/env python3
"""LLM-judged evaluation of Layer 3 synthesis articles.

Reads a rubric markdown file plus one or more synthesis .md files,
calls Sonnet to grade each article against each criterion, and prints
(or saves) JSON results.

Usage:
    # Grade a single article, print to stdout
    python scripts/grade-synthesis.py tests/synthesis_corpus/latest/legal.md

    # Grade everything in latest/, save results under grades/<date>/
    python scripts/grade-synthesis.py \
        --batch tests/synthesis_corpus/latest \
        --out tests/synthesis_corpus/grades/$(date -u +%Y-%m-%d)

    # Use a different rubric file
    python scripts/grade-synthesis.py --rubric path/to/alt-rubric.md article.md

Results are small JSON files with one record per article:

    {
        "article": "tests/synthesis_corpus/latest/legal.md",
        "rubric_sha": "<first 12 chars of sha256>",
        "graded_at": "2026-04-10T13:14:15Z",
        "judge_model": "claude-sonnet-4-6",
        "scores": {
            "summary_quality":             {"score": 9, "justification": "..."},
            "editorial_voice":             {"score": 8, "justification": "..."},
            ...
        },
        "average": 8.3,
        "total": 83
    }

The judge is non-deterministic, so treat scores as signal, not ground
truth. Trust the deltas between runs more than absolute scores.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import anthropic


ROOT = Path(__file__).parent.parent
DEFAULT_RUBRIC = ROOT / "tests" / "synthesis_corpus" / "rubric.md"
JUDGE_MODEL = "claude-sonnet-4-6"


SYSTEM_PROMPT = """You are grading a Layer 3 synthesis article produced by
the Meridian knowledge pipeline. You will be given:

1. A rubric with named criteria, each scored 0-10.
2. The article under review, including YAML frontmatter.

For each criterion in the rubric, output a score and a one-sentence
justification grounded in a specific observation about the article
(quoting a phrase where helpful). Do not invent criteria. Do not grade
criteria that are not in the rubric. Do not change the scoring scale.

Output strictly valid JSON with this shape:

{
  "scores": {
    "<criterion_id>": {"score": <int 0-10>, "justification": "<one sentence>"},
    ...
  }
}

Use the criterion IDs exactly as they appear in the rubric (the `###`
headers, lowercased with underscores preserved). Do not include any
prose outside the JSON object. Do not wrap the JSON in a markdown code
fence."""


def _sha12(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _strip_json_fence(text: str) -> str:
    """Defend against the judge returning a fenced JSON block anyway."""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*\n?", "", t)
        t = re.sub(r"\n?```\s*$", "", t)
    return t


def grade_article(
    client: anthropic.Anthropic,
    rubric_text: str,
    article_text: str,
    judge_model: str = JUDGE_MODEL,
) -> dict:
    """Call the judge and return parsed scores."""
    user_content = (
        "## Rubric\n\n"
        f"{rubric_text}\n\n"
        "## Article under review\n\n"
        f"{article_text}"
    )
    response = client.messages.create(
        model=judge_model,
        max_tokens=4096,
        temperature=0.0,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    )
    raw = response.content[0].text
    try:
        parsed = json.loads(_strip_json_fence(raw))
    except json.JSONDecodeError as e:
        return {
            "error": f"failed to parse judge JSON: {e}",
            "raw_response": raw[:2000],
        }
    scores = parsed.get("scores") or {}
    if not scores:
        return {"error": "judge returned no scores", "raw_response": raw[:2000]}
    numeric = [v.get("score") for v in scores.values() if isinstance(v.get("score"), (int, float))]
    return {
        "scores": scores,
        "average": round(sum(numeric) / len(numeric), 2) if numeric else None,
        "total": sum(numeric) if numeric else None,
    }


def grade_file(
    article_path: Path,
    rubric_text: str,
    rubric_sha: str,
    client: anthropic.Anthropic,
    judge_model: str,
) -> dict:
    article_text = article_path.read_text(encoding="utf-8", errors="replace")
    result = grade_article(client, rubric_text, article_text, judge_model=judge_model)
    return {
        "article": str(article_path),
        "rubric_sha": rubric_sha,
        "graded_at": _now_iso(),
        "judge_model": judge_model,
        **result,
    }


def _collect_articles(args) -> list[Path]:
    paths: list[Path] = []
    if args.batch:
        batch_dir = Path(args.batch)
        if not batch_dir.is_dir():
            print(f"Not a directory: {batch_dir}", file=sys.stderr)
            sys.exit(2)
        paths = sorted(batch_dir.glob("*.md"))
    paths.extend(Path(p) for p in args.articles)
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description="Grade synthesis articles against a rubric")
    parser.add_argument("articles", nargs="*", help="Article markdown files to grade")
    parser.add_argument(
        "--batch", help="Grade every .md file in this directory (in addition to explicit articles)"
    )
    parser.add_argument("--rubric", help="Path to rubric markdown file", default=str(DEFAULT_RUBRIC))
    parser.add_argument("--out", help="Write one JSON result per article into this directory")
    parser.add_argument("--judge-model", default=JUDGE_MODEL)
    args = parser.parse_args()

    rubric_path = Path(args.rubric)
    if not rubric_path.exists():
        print(f"Rubric not found: {rubric_path}", file=sys.stderr)
        sys.exit(2)
    rubric_text = rubric_path.read_text(encoding="utf-8")
    rubric_sha = _sha12(rubric_text)

    articles = _collect_articles(args)
    if not articles:
        parser.error("no articles supplied — pass paths or --batch <dir>")

    out_dir = Path(args.out) if args.out else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)

    client = anthropic.Anthropic()
    summary: list[dict] = []

    for path in articles:
        if not path.exists():
            print(f"  SKIP {path} (missing)", file=sys.stderr)
            continue
        print(f"  grading {path.name}…", file=sys.stderr)
        t0 = time.time()
        record = grade_file(path, rubric_text, rubric_sha, client, args.judge_model)
        record["elapsed_s"] = round(time.time() - t0, 1)

        if out_dir:
            out_path = out_dir / f"{path.stem}.json"
            out_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
        else:
            print(json.dumps(record, indent=2))

        summary.append(record)

    if summary and out_dir:
        # Print a compact leaderboard to stderr so shell users can see at a glance.
        print("\n== Leaderboard ==", file=sys.stderr)
        for rec in sorted(summary, key=lambda r: r.get("average") or 0, reverse=True):
            avg = rec.get("average")
            name = Path(rec["article"]).stem
            if avg is None:
                print(f"  {name:<20} ERROR", file=sys.stderr)
            else:
                print(f"  {name:<20} avg={avg:5.2f}  total={rec.get('total')}", file=sys.stderr)


if __name__ == "__main__":
    main()
