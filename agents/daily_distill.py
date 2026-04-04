#!/usr/bin/env python3
"""Meridian Daily Distill — review capture/ and promote worthy docs to raw/.

Usage:
    python agents/daily_distill.py                    # review all unprocessed
    python agents/daily_distill.py --file capture/foo.md  # review a specific file
    python agents/daily_distill.py --approve capture/foo.md  # approve a pending promotion
    python agents/daily_distill.py --dry-run           # score without writing

Output: JSON summary of decisions to stdout.
"""

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import anthropic
import yaml


ROOT = Path(__file__).parent.parent
CAPTURE_DIR = ROOT / "capture"
RAW_DIR = ROOT / "raw"
PROMPTS_DIR = ROOT / "prompts"


def load_config() -> dict:
    with open(ROOT / "config.yaml") as f:
        return yaml.safe_load(f)


def load_prompt() -> str:
    return (PROMPTS_DIR / "daily_distill.md").read_text(encoding="utf-8")


def get_unprocessed_files() -> list[Path]:
    """Find capture/ files that haven't been processed by distill yet."""
    files = []
    for f in sorted(CAPTURE_DIR.glob("*.md")):
        content = f.read_text(encoding="utf-8", errors="replace")
        if "distill_status:" not in content:
            files.append(f)
    return files


def score_document(client: anthropic.Anthropic, content: str, config: dict) -> dict:
    """Send document to LLM for scoring."""
    system_prompt = load_prompt()

    response = client.messages.create(
        model=config["llm"]["model"],
        max_tokens=config["llm"]["max_tokens"],
        temperature=config["llm"]["temperature"],
        system=system_prompt,
        messages=[{
            "role": "user",
            "content": f"Review this document:\n\n{content}"
        }],
    )

    # Parse JSON from response
    text = response.content[0].text
    # Extract JSON if wrapped in markdown code block
    json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if json_match:
        text = json_match.group(1)

    return json.loads(text)


def mark_processed(filepath: Path, decision: dict):
    """Add distill metadata to the capture file's frontmatter."""
    content = filepath.read_text(encoding="utf-8")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    distill_block = (
        f"distill_status: {decision['decision']}\n"
        f"distill_date: \"{now}\"\n"
        f"distill_score:\n"
        f"  relevance: {decision['relevance']}\n"
        f"  quality: {decision['quality']}\n"
    )

    if content.startswith("---"):
        # Insert before closing ---
        parts = content.split("---", 2)
        if len(parts) >= 3:
            parts[1] = parts[1].rstrip() + "\n" + distill_block
            content = "---".join(parts)
    else:
        # No frontmatter — prepend
        content = f"---\n{distill_block}---\n\n{content}"

    filepath.write_text(content, encoding="utf-8")


def promote_to_raw(capture_path: Path, decision: dict) -> Path:
    """Copy document to raw/ with normalized frontmatter."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    content = capture_path.read_text(encoding="utf-8")

    # Strip existing frontmatter to replace with normalized version
    body = content
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            body = parts[2].strip()

    fm = decision.get("frontmatter", {})
    if not fm:
        fm = {}

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    raw_frontmatter = {
        "title": fm.get("title", capture_path.stem),
        "source_url": fm.get("source_url", ""),
        "source_type": fm.get("source_type", "note"),
        "date_ingested": now,
        "compiled_at": "",
        "tags": fm.get("tags", []),
        "summary": fm.get("summary", ""),
    }

    raw_content = "---\n" + yaml.dump(raw_frontmatter, default_flow_style=False, sort_keys=False).strip() + "\n---\n\n" + body + "\n"

    # Use same filename
    raw_path = RAW_DIR / capture_path.name
    raw_path.write_text(raw_content, encoding="utf-8")

    return raw_path


def main():
    parser = argparse.ArgumentParser(description="Meridian Daily Distill")
    parser.add_argument("--file", help="Process a specific capture file")
    parser.add_argument("--approve", help="Approve a pending promotion")
    parser.add_argument("--dry-run", action="store_true", help="Score without writing")
    args = parser.parse_args()

    config = load_config()
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env

    results = []

    if args.approve:
        # Direct approval — promote without re-scoring
        path = Path(args.approve)
        if not path.exists():
            print(f"Error: {path} not found", file=sys.stderr)
            sys.exit(1)
        raw_path = promote_to_raw(path, {"frontmatter": None})
        mark_processed(path, {
            "decision": "promoted",
            "relevance": 10,
            "quality": 10,
        })
        results.append({"file": str(path), "action": "approved", "raw_path": str(raw_path)})

    else:
        # Score and decide
        if args.file:
            files = [Path(args.file)]
        else:
            files = get_unprocessed_files()

        if not files:
            print("No unprocessed files in capture/", file=sys.stderr)
            print(json.dumps({"status": "ok", "processed": 0, "results": []}))
            return

        for filepath in files:
            print(f"Reviewing {filepath.name}...", file=sys.stderr)
            content = filepath.read_text(encoding="utf-8", errors="replace")

            try:
                decision = score_document(client, content, config)
            except Exception as e:
                print(f"Error scoring {filepath.name}: {e}", file=sys.stderr)
                results.append({"file": str(filepath), "error": str(e)})
                continue

            result = {
                "file": str(filepath),
                "decision": decision["decision"],
                "relevance": decision["relevance"],
                "quality": decision["quality"],
                "reasoning": decision.get("reasoning", ""),
            }

            if not args.dry_run:
                mark_processed(filepath, decision)

                auto_threshold = config["distill"]["auto_promote_threshold"]
                if (decision["decision"] == "promote"
                        and decision["relevance"] >= auto_threshold
                        and decision["quality"] >= auto_threshold):
                    # Auto-promote high-confidence items
                    raw_path = promote_to_raw(filepath, decision)
                    result["action"] = "auto_promoted"
                    result["raw_path"] = str(raw_path)
                elif decision["decision"] == "promote":
                    # Needs approval — leave in capture/ with distill metadata
                    result["action"] = "pending_approval"
                else:
                    result["action"] = "skipped"

            results.append(result)

    output = {
        "status": "ok",
        "processed": len(results),
        "results": results,
    }
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
