#!/usr/bin/env python3
"""Meridian Debrief Agent — extract learnings from Claude Code session transcripts.

Usage:
    python agents/debrief.py                          # debrief most recent session
    python agents/debrief.py --session SESSION_ID     # debrief specific session
    python agents/debrief.py --file capture/foo.md    # debrief specific file

Reads a Claude Code session transcript from capture/, sends it to the LLM for
analysis, and writes a structured debrief to capture/ as a separate .md file.

Output: JSON with status and path to the debrief file.
"""

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import anthropic
import yaml


ROOT = Path(__file__).parent.parent
CAPTURE_DIR = ROOT / "capture"
PROMPTS_DIR = ROOT / "prompts"


def load_config() -> dict:
    with open(ROOT / "config.yaml") as f:
        return yaml.safe_load(f)


def load_prompt() -> str:
    return (PROMPTS_DIR / "debrief.md").read_text(encoding="utf-8")


def find_session(session_id: str | None) -> Path | None:
    """Find a claude-session file in capture/."""
    candidates = sorted(CAPTURE_DIR.glob("*claude-session*.md"), reverse=True)
    if not candidates:
        return None

    if session_id:
        for c in candidates:
            content = c.read_text(encoding="utf-8", errors="replace")
            if session_id in content or session_id in c.name:
                return c
        return None

    # Most recent
    return candidates[0]


def run_debrief(filepath: Path, config: dict) -> dict:
    """Send session transcript to LLM for debrief analysis."""
    client = anthropic.Anthropic()
    system_prompt = load_prompt()

    content = filepath.read_text(encoding="utf-8", errors="replace")
    # Truncate very long transcripts to fit context
    if len(content) > 100_000:
        content = content[:100_000] + "\n\n[... transcript truncated at 100k chars ...]"

    response = client.messages.create(
        model=config["llm"]["model"],
        max_tokens=config["llm"]["max_tokens"],
        temperature=config["llm"]["temperature"],
        system=system_prompt,
        messages=[{
            "role": "user",
            "content": f"Debrief this Claude Code session:\n\n{content}",
        }],
    )

    return {"analysis": response.content[0].text}


def write_debrief(source_path: Path, analysis: str) -> Path:
    """Write the debrief output to capture/ as a structured .md."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    content_hash = hashlib.md5(analysis.encode()).hexdigest()[:6]

    # Extract project name from source filename
    project = "unknown"
    match = re.search(r"claude-session-(.+?)-[a-f0-9]{6}\.md$", source_path.name)
    if match:
        project = match.group(1)

    frontmatter = yaml.dump({
        "title": f"Session Debrief — {project}",
        "source_url": "",
        "source_type": "claude-session",
        "date_captured": now,
        "debrief_of": source_path.name,
        "project": project,
        "tags": ["debrief", "claude-session", project],
    }, default_flow_style=False, sort_keys=False).strip()

    md = f"---\n{frontmatter}\n---\n\n{analysis}\n"
    filename = f"{now}-debrief-{project}-{content_hash}.md"
    out_path = CAPTURE_DIR / filename
    out_path.write_text(md, encoding="utf-8")
    return out_path


def main():
    parser = argparse.ArgumentParser(description="Meridian Session Debrief")
    parser.add_argument("--session", help="Session ID to debrief")
    parser.add_argument("--file", help="Specific capture file to debrief")
    args = parser.parse_args()

    config = load_config()

    if args.file:
        filepath = Path(args.file)
        if not filepath.exists():
            print(f"Error: {filepath} not found", file=sys.stderr)
            sys.exit(1)
    else:
        filepath = find_session(args.session)
        if not filepath:
            msg = f"session {args.session}" if args.session else "any claude-session"
            print(f"Error: no {msg} found in capture/", file=sys.stderr)
            sys.exit(1)

    print(f"Debriefing {filepath.name}...", file=sys.stderr)
    result = run_debrief(filepath, config)
    out_path = write_debrief(filepath, result["analysis"])
    print(f"Debrief written to {out_path.name}", file=sys.stderr)

    output = {
        "status": "ok",
        "source": str(filepath),
        "debrief": str(out_path),
        "result": result["analysis"],
    }
    print(json.dumps(output))


if __name__ == "__main__":
    main()
