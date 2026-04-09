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


def mark_processed(filepath: Path, decision: dict, error: str | None = None) -> None:
    """Add distill metadata to the capture file's frontmatter.

    When `error` is set, records a `distill_status: error` marker alongside
    the error message so downstream tooling can tell scored items apart from
    items that fell through unscored.
    """
    content = filepath.read_text(encoding="utf-8")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if error:
        distill_block = (
            f"distill_status: error\n"
            f"distill_date: \"{now}\"\n"
            f"distill_error: {json.dumps(error)}\n"
        )
    else:
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


# Provenance and dedup keys carried over from capture/ into raw/. These keys
# are the source of truth for `find_gdrive_file` and equivalent dedup scans,
# so they MUST survive normalization. Add new dedup keys here, not inline.
PROVENANCE_KEYS: tuple[str, ...] = (
    "source_url",
    "source_type",
    "gdrive_file_id",
    "gdrive_folder",
    "recording_id",
    "share_url",
    "session_id",
    "project",
    "meeting_date",
    "owner",
    "modified_at",
    "word_count",
    "attendees",
)


def _parse_frontmatter(content: str) -> tuple[dict, str]:
    """Split a markdown doc into (frontmatter dict, body). Empty dict if none."""
    if not content.startswith("---"):
        return {}, content
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content
    try:
        fm = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        fm = {}
    if not isinstance(fm, dict):
        fm = {}
    return fm, parts[2].lstrip("\n")


def promote_to_raw(capture_path: Path, decision: dict) -> Path:
    """Copy document to raw/ with normalized frontmatter.

    Provenance / dedup fields (gdrive_file_id, recording_id, etc.) are carried
    over from the capture file so that `find_gdrive_file`-style scans over
    raw/ continue to work after the file has left capture/.
    """
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    content = capture_path.read_text(encoding="utf-8")
    source_fm, body = _parse_frontmatter(content)
    decision_fm = decision.get("frontmatter") or {}

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    raw_frontmatter: dict = {
        "title": source_fm.get("title") or decision_fm.get("title") or capture_path.stem,
        "source_type": source_fm.get("source_type") or decision_fm.get("source_type") or "note",
        "source_url": source_fm.get("source_url") or decision_fm.get("source_url", ""),
        "date_ingested": now,
        "compiled_at": "",
        "tags": source_fm.get("tags") or decision_fm.get("tags") or [],
        "summary": source_fm.get("summary") or decision_fm.get("summary", ""),
    }

    # Carry over remaining provenance / dedup keys verbatim from the capture
    # frontmatter. Only include keys that actually have values so the raw
    # frontmatter stays readable.
    for key in PROVENANCE_KEYS:
        if key in raw_frontmatter:
            continue
        value = source_fm.get(key)
        if value not in (None, "", [], {}):
            raw_frontmatter[key] = value

    raw_content = (
        "---\n"
        + yaml.dump(raw_frontmatter, default_flow_style=False, sort_keys=False).strip()
        + "\n---\n\n"
        + body.strip()
        + "\n"
    )

    # Use same filename
    raw_path = RAW_DIR / capture_path.name
    raw_path.write_text(raw_content, encoding="utf-8")

    return raw_path


def main():
    parser = argparse.ArgumentParser(description="Meridian Daily Distill")
    parser.add_argument("--file", help="Process a specific capture file")
    parser.add_argument("--approve", help="Approve a pending promotion")
    parser.add_argument(
        "--promote-all",
        action="store_true",
        help="Promote every file in capture/ to raw/ without scoring (recovery mode)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Score without writing")
    args = parser.parse_args()

    config = load_config()

    results: list[dict] = []

    if args.promote_all:
        # Recovery path: bypass scoring and push everything in capture/ to raw/.
        # Use this when capture is wedged with legacy metadata or when scoring
        # has been unavailable (e.g. LLM outage).
        for path in sorted(CAPTURE_DIR.glob("*.md")):
            try:
                raw_path = promote_to_raw(path, {"frontmatter": None})
                path.unlink()
                results.append({
                    "file": str(path),
                    "action": "force_promoted",
                    "raw_path": str(raw_path),
                })
            except Exception as e:
                print(f"Failed to promote {path.name}: {e}", file=sys.stderr)
                results.append({"file": str(path), "error": str(e)})
        print(json.dumps({"status": "ok", "processed": len(results), "results": results}, indent=2))
        return

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env

    if args.approve:
        # Direct approval — promote without re-scoring
        path = Path(args.approve)
        if not path.exists():
            print(f"Error: {path} not found", file=sys.stderr)
            sys.exit(1)
        raw_path = promote_to_raw(path, {"frontmatter": None})
        results.append({"file": str(path), "action": "approved", "raw_path": str(raw_path)})
        path.unlink()

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

            # Score the document. Failures are logged as metadata but never
            # block promotion — Sieve (upstream) is the human review gate, so
            # everything that reaches Meridian's capture/ should flow through.
            decision: dict | None = None
            score_error: str | None = None
            try:
                decision = score_document(client, content, config)
            except Exception as e:
                score_error = str(e)
                print(f"Error scoring {filepath.name}: {e}", file=sys.stderr)

            if decision is None:
                # Synthesize a minimal decision record so downstream code has
                # consistent shape and the raw/ frontmatter carries the error.
                decision = {
                    "decision": "promote",
                    "relevance": 0,
                    "quality": 0,
                    "reasoning": f"scoring_error: {score_error}",
                    "error": score_error,
                }

            result = {
                "file": str(filepath),
                "decision": decision.get("decision", "promote"),
                "relevance": decision.get("relevance", 0),
                "quality": decision.get("quality", 0),
                "reasoning": decision.get("reasoning", ""),
            }
            if score_error:
                result["error"] = score_error

            if not args.dry_run:
                mark_processed(filepath, decision, error=score_error)

                # Always promote. Human review already happened in Sieve;
                # Meridian's job is to ingest everything it receives.
                raw_path = promote_to_raw(filepath, decision)
                result["action"] = "promoted" if not score_error else "promoted_with_error"
                result["raw_path"] = str(raw_path)
                filepath.unlink()

            results.append(result)

    output = {
        "status": "ok",
        "processed": len(results),
        "results": results,
    }
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
