#!/usr/bin/env python3
"""Meridian Compiler — compile raw/ documents into wiki/ articles.

Usage:
    python agents/compiler.py                        # compile all uncompiled raw docs
    python agents/compiler.py --file raw/foo.md      # compile a specific file

Reads AGENTS.md and wiki/_index.md for context, then sends each raw document
to the LLM for compilation into wiki articles.

Output: JSON summary of what was created/updated.
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import anthropic
import yaml


ROOT = Path(__file__).parent.parent
RAW_DIR = ROOT / "raw"
WIKI_DIR = ROOT / "wiki"
PROMPTS_DIR = ROOT / "prompts"


def load_config() -> dict:
    with open(ROOT / "config.yaml") as f:
        return yaml.safe_load(f)


def load_prompt() -> str:
    return (PROMPTS_DIR / "compiler.md").read_text(encoding="utf-8")


def load_agents_md() -> str:
    agents_path = ROOT / "AGENTS.md"
    if agents_path.exists():
        return agents_path.read_text(encoding="utf-8")
    return ""


def load_index() -> str:
    index_path = WIKI_DIR / "_index.md"
    if index_path.exists():
        return index_path.read_text(encoding="utf-8")
    return "_No index yet._"


def get_uncompiled_files() -> list[Path]:
    """Find raw/ files that haven't been compiled yet."""
    files = []
    for f in sorted(RAW_DIR.glob("*.md")):
        if f.name.startswith("_"):
            continue
        content = f.read_text(encoding="utf-8", errors="replace")
        if "compiled_at:" in content:
            # Check if compiled_at is empty
            match = re.search(r"compiled_at:\s*['\"]?([^'\"\n]*)", content)
            if match and match.group(1).strip() in ("", "null", "~"):
                files.append(f)
        else:
            files.append(f)
    return files


def compile_document(client: anthropic.Anthropic, raw_content: str,
                     agents_md: str, index_md: str, config: dict) -> dict:
    """Send a raw document to the LLM for compilation."""
    system_prompt = load_prompt()

    user_content = (
        f"## AGENTS.md\n\n{agents_md}\n\n"
        f"## wiki/_index.md\n\n{index_md}\n\n"
        f"## Raw Document to Compile\n\n{raw_content}"
    )

    response = client.messages.create(
        model=config["llm"]["model"],
        max_tokens=config["llm"]["max_tokens"],
        temperature=config["llm"]["temperature"],
        system=system_prompt,
        messages=[{
            "role": "user",
            "content": user_content,
        }],
    )

    text = response.content[0].text
    # Extract JSON from response
    json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if json_match:
        text = json_match.group(1)

    return json.loads(text)


def write_compiled_files(result: dict) -> list[str]:
    """Write the files the compiler produced."""
    written = []
    for file_entry in result.get("files", []):
        path = ROOT / file_entry["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(file_entry["content"], encoding="utf-8")
        written.append(str(path))
    return written


def update_index(result: dict):
    """Update wiki/_index.md with new entries."""
    index_path = WIKI_DIR / "_index.md"
    index_update = result.get("index_update", "")
    if not index_update:
        return

    if index_path.exists():
        content = index_path.read_text(encoding="utf-8")
    else:
        content = "---\ntitle: Meridian Wiki Index\ntype: index\n---\n\n# Meridian Wiki Index\n"

    # Remove the "No articles yet" placeholder
    content = content.replace("_No articles yet. The wiki is in bootstrap mode._\n", "")
    content = content.replace("_No concepts yet._\n", "")
    content = content.replace("_No categories yet._\n", "")

    # Update statistics
    article_count = sum(1 for _ in WIKI_DIR.rglob("*.md")
                        if _.name not in ("_index.md", "_backlinks.md", "log.md"))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    content = re.sub(r"Total articles: \d+", f"Total articles: {article_count}", content)
    content = re.sub(r"Last updated: \d{4}-\d{2}-\d{2}", f"Last updated: {now}", content)

    # Append new entries before statistics
    if "## Statistics" in content:
        content = content.replace("## Statistics", f"{index_update}\n\n## Statistics")
    else:
        content += f"\n{index_update}\n"

    index_path.write_text(content, encoding="utf-8")


def update_backlinks(result: dict):
    """Update wiki/_backlinks.md."""
    backlinks = result.get("backlinks", [])
    if not backlinks:
        return

    bl_path = WIKI_DIR / "_backlinks.md"
    if bl_path.exists():
        content = bl_path.read_text(encoding="utf-8")
    else:
        content = "---\ntitle: Backlink Registry\ntype: index\n---\n\n# Backlink Registry\n"

    content = content.replace("_No backlinks yet._\n", "")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for bl in backlinks:
        entry = f"- [{bl['from']}]({bl['from']}) → [{bl['to']}]({bl['to']})\n"
        if entry not in content:
            content += entry

    content = re.sub(r"updated: \"\d{4}-\d{2}-\d{2}\"", f'updated: "{now}"', content)
    bl_path.write_text(content, encoding="utf-8")


def mark_compiled(filepath: Path):
    """Set compiled_at in the raw document's frontmatter."""
    content = filepath.read_text(encoding="utf-8")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if "compiled_at:" in content:
        content = re.sub(r"compiled_at:.*", f"compiled_at: '{now}'", content)
    elif content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            parts[1] = parts[1].rstrip() + f"\ncompiled_at: '{now}'\n"
            content = "---".join(parts)

    filepath.write_text(content, encoding="utf-8")


def append_log(message: str):
    """Append an entry to wiki/log.md."""
    log_path = WIKI_DIR / "log.md"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if log_path.exists():
        content = log_path.read_text(encoding="utf-8")
    else:
        WIKI_DIR.mkdir(parents=True, exist_ok=True)
        content = "---\ntitle: Meridian Operations Log\ntype: index\n---\n\n# Operations Log\n"

    content = content.replace("_No entries yet._\n", "")
    content += f"\n## [{now}] compile | {message}\n"
    log_path.write_text(content, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Meridian Compiler")
    parser.add_argument("--file", help="Specific raw file to compile")
    args = parser.parse_args()

    config = load_config()
    client = anthropic.Anthropic()
    agents_md = load_agents_md()

    if args.file:
        files = [Path(args.file)]
    else:
        files = get_uncompiled_files()

    if not files:
        print("No uncompiled files in raw/", file=sys.stderr)
        print(json.dumps({"status": "ok", "compiled": 0, "results": []}))
        return

    results = []

    for filepath in files:
        print(f"Compiling {filepath.name}...", file=sys.stderr)

        raw_content = filepath.read_text(encoding="utf-8", errors="replace")
        index_md = load_index()  # reload each time since we update it

        try:
            result = compile_document(client, raw_content, agents_md, index_md, config)
        except json.JSONDecodeError as e:
            print(f"Error parsing compiler output for {filepath.name}: {e}", file=sys.stderr)
            results.append({"file": str(filepath), "error": f"JSON parse error: {e}"})
            continue
        except Exception as e:
            print(f"Error compiling {filepath.name}: {e}", file=sys.stderr)
            results.append({"file": str(filepath), "error": str(e)})
            continue

        # Check for bootstrap proposal
        proposal = result.get("proposal")
        if proposal:
            results.append({
                "file": str(filepath),
                "action": "proposed",
                "proposal": proposal,
                "files": [f["path"] for f in result.get("files", [])],
            })
            # In bootstrap mode, still write the files since we're running it manually
            written = write_compiled_files(result)
            update_index(result)
            update_backlinks(result)
            mark_compiled(filepath)
            log_msg = f'Compiled "{filepath.name}" → {", ".join(written)}'
            append_log(log_msg)
            continue

        # Steady state — write directly
        written = write_compiled_files(result)
        update_index(result)
        update_backlinks(result)
        mark_compiled(filepath)

        log_msg = f'Compiled "{filepath.name}" → {", ".join(written)}'
        append_log(log_msg)

        results.append({
            "file": str(filepath),
            "action": result.get("action", "create"),
            "written": written,
        })

    output = {
        "status": "ok",
        "compiled": len(results),
        "results": results,
    }
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
