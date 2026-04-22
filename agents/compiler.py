#!/usr/bin/env python3
"""Meridian Compiler — compile raw/ documents into wiki/ articles.

Two-pass architecture:
  Pass 1 (Planning): Haiku decides what files to create and where (fast)
  Pass 2 (Writing):  Sonnet writes the actual content (parallel, 3 workers)

Usage:
    python agents/compiler.py                        # compile all uncompiled raw docs
    python agents/compiler.py --file raw/foo.md      # compile a specific file

Output: JSON summary of what was created/updated.
"""

import argparse
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import anthropic
import yaml


ROOT = Path(__file__).parent.parent
RAW_DIR = ROOT / "raw"
WIKI_DIR = ROOT / "wiki"
PROMPTS_DIR = ROOT / "prompts"

# Thread-safe lock for file writes
_write_lock = threading.Lock()


def load_config() -> dict:
    with open(ROOT / "config.yaml") as f:
        return yaml.safe_load(f)


def load_agents_md() -> str:
    path = ROOT / "AGENTS.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def load_index() -> str:
    path = WIKI_DIR / "_index.md"
    return path.read_text(encoding="utf-8") if path.exists() else "_No index yet._"


def load_registry(filename: str) -> str:
    """Load a YAML registry and return a compact version for the LLM."""
    path = ROOT / filename
    if not path.exists():
        return ""
    with open(path) as f:
        data = yaml.safe_load(f) or {}

    # Each registry has a different top-level key. Handle all three.
    items = (
        data.get("clients")
        or data.get("categories")
        or data.get("industries")
        or []
    )
    lines = []
    for item in items:
        name = item.get("name", "")
        slug = item.get("slug", "")
        aliases = item.get("aliases", [])
        alias_str = ", ".join(aliases[:10]) if aliases else ""
        if "clients" in filename:
            status = item.get("status", "")
            industry = item.get("industry", "")
            industry_suffix = f" → {industry}" if industry else ""
            lines.append(f"- {slug} ({status}{industry_suffix}): \"{name}\" [{alias_str}]")
        elif "industries" in filename:
            lines.append(f"- {slug}: \"{name}\" [{alias_str}]")
        else:
            cat = item.get("category", "")
            lines.append(f"- {slug} ({cat}): \"{name}\" [{alias_str}]")
    return "\n".join(lines)


def load_registry_data(filename: str) -> dict:
    """Load a YAML registry as parsed data for validation."""
    path = ROOT / filename
    if path.exists():
        with open(path) as f:
            return yaml.safe_load(f) or {}
    return {}


def build_slug_lookup(registry_data: dict, key: str) -> dict[str, str]:
    """Build a lookup: alias → canonical slug from a registry.

    Handles the three possible top-level keys ("clients", "categories",
    "industries") so one helper works for all three dimensions.
    """
    lookup = {}
    items = (
        registry_data.get(key)
        or registry_data.get("clients")
        or registry_data.get("categories")
        or registry_data.get("industries")
        or []
    )
    for item in items:
        slug = item.get("slug", "")
        lookup[slug] = slug
        lookup[item.get("name", "").lower()] = slug
        for alias in item.get("aliases", []):
            lookup[alias.lower()] = slug
    return lookup


def build_client_industry_map(clients_data: dict) -> dict[str, str]:
    """Return {client_slug: industry_slug} for every client with an industry tag.

    The compiler passes this to the planner so the LLM knows which industry
    to cross-file a client-specific insight into. Clients without an
    industry field are skipped — their fragments won't be cross-filed.
    """
    out: dict[str, str] = {}
    for item in clients_data.get("clients", []):
        if not isinstance(item, dict):
            continue
        slug = item.get("slug")
        industry = item.get("industry")
        if slug and industry:
            out[slug] = industry
    return out


def get_uncompiled_files() -> list[Path]:
    """Find raw/ files that haven't been compiled yet."""
    files = []
    for f in sorted(RAW_DIR.glob("*.md")):
        if f.name.startswith("_"):
            continue
        content = f.read_text(encoding="utf-8", errors="replace")
        if "compiled_at:" in content:
            match = re.search(r"compiled_at:\s*['\"]?([^'\"\n]*)", content)
            if match and match.group(1).strip() in ("", "null", "~"):
                files.append(f)
        else:
            files.append(f)
    return files


# ---------------------------------------------------------------------------
# Pass 1: Planning (Haiku — fast)
# ---------------------------------------------------------------------------

def plan_document(client: anthropic.Anthropic, raw_content: str,
                  index_md: str, clients_yaml: str, topics_yaml: str,
                  industries_yaml: str, client_industry_map: dict[str, str],
                  config: dict) -> dict:
    """Use Haiku to decide what files to create and where."""
    system_prompt = (PROMPTS_DIR / "compiler_plan.md").read_text(encoding="utf-8")
    planning_model = config.get("compiler", {}).get(
        "planning_model", "claude-haiku-4-5-20251001"
    )

    # Render the client → industry mapping as a compact lookup so the
    # planner can resolve "BluePoint" → "financial-services" without
    # having to re-derive it from clients.yaml.
    if client_industry_map:
        industry_map_lines = "\n".join(
            f"- {slug} → {industry}"
            for slug, industry in sorted(client_industry_map.items())
        )
    else:
        industry_map_lines = "(empty)"

    response = client.messages.create(
        model=planning_model,
        max_tokens=16384,
        temperature=0.2,
        system=system_prompt,
        messages=[{
            "role": "user",
            "content": (
                f"## wiki/_index.md\n\n{index_md}\n\n"
                f"## Client Registry (clients.yaml)\n\n{clients_yaml}\n\n"
                f"## Topic Registry (topics.yaml)\n\n{topics_yaml}\n\n"
                f"## Industry Registry (industries.yaml)\n\n{industries_yaml}\n\n"
                f"## Client → Industry Lookup\n\n{industry_map_lines}\n\n"
                f"## Raw Document to Compile\n\n{raw_content}"
            ),
        }],
    )

    if response.stop_reason == "max_tokens":
        raise ValueError(
            f"planner hit max_tokens={16384} (output truncated mid-JSON) — "
            f"raw doc may be too dense; consider raising the cap"
        )

    text = response.content[0].text
    json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if json_match:
        text = json_match.group(1)
    return json.loads(text)


def validate_plan(
    plan: dict,
    client_lookup: dict,
    topic_lookup: dict,
    industry_lookup: dict | None = None,
) -> dict:
    """Validate plan entries against registries. Fix or flag invalid paths."""
    validated_entries = []
    warnings = []
    industry_lookup = industry_lookup or {}

    for entry in plan.get("plan", []):
        path = entry.get("path", "")

        # Validate client paths
        if "/clients/" in path:
            # Extract the client slug from path like wiki/clients/current/slug/file.md
            parts = path.split("/")
            if len(parts) >= 5:
                client_slug = parts[3]  # wiki/clients/status/SLUG/...
                if client_slug not in client_lookup.values():
                    # Try to find a match
                    matched = client_lookup.get(client_slug.lower())
                    if matched:
                        parts[3] = matched
                        entry["path"] = "/".join(parts)
                    else:
                        warnings.append(f"Skipped {path}: client '{client_slug}' not in registry")
                        continue

        # Validate knowledge paths
        if "/knowledge/" in path:
            parts = path.split("/")
            if len(parts) >= 4:
                topic_slug = parts[2]  # wiki/knowledge/SLUG/...
                if topic_slug not in topic_lookup.values():
                    matched = topic_lookup.get(topic_slug.lower())
                    if matched:
                        parts[2] = matched
                        entry["path"] = "/".join(parts)
                    else:
                        warnings.append(f"Skipped {path}: topic '{topic_slug}' not in registry")
                        continue

        # Validate industry paths — the third cross-filing dimension.
        # Same enforcement model as clients/topics: reject any industry
        # slug that isn't in industries.yaml, try an alias match before
        # giving up. If no industry lookup was provided (legacy call
        # site), pass industry paths through unchanged.
        if "/industries/" in path and industry_lookup:
            parts = path.split("/")
            if len(parts) >= 4:
                industry_slug = parts[2]  # wiki/industries/SLUG/...
                if industry_slug not in industry_lookup.values():
                    matched = industry_lookup.get(industry_slug.lower())
                    if matched:
                        parts[2] = matched
                        entry["path"] = "/".join(parts)
                    else:
                        warnings.append(
                            f"Skipped {path}: industry '{industry_slug}' not in registry"
                        )
                        continue

        validated_entries.append(entry)

    plan["plan"] = validated_entries
    if warnings:
        plan["validation_warnings"] = warnings
    return plan


# ---------------------------------------------------------------------------
# Pass 2: Writing (Sonnet — parallel)
# ---------------------------------------------------------------------------

def write_single_file(client: anthropic.Anthropic, raw_content: str,
                      plan_entry: dict, raw_filename: str,
                      config: dict) -> dict:
    """Use Sonnet to write one wiki file from the plan."""
    system_prompt = (PROMPTS_DIR / "compiler_write.md").read_text(encoding="utf-8")
    writing_model = config.get("compiler", {}).get(
        "writing_model", "claude-sonnet-4-6"
    )

    user_content = (
        f"## Raw Document\n\n{raw_content}\n\n"
        f"## Filing Plan Entry\n\n"
        f"- **Path:** {plan_entry['path']}\n"
        f"- **Action:** {plan_entry.get('action', 'create')}\n"
        f"- **Type:** {plan_entry.get('type', 'article')}\n"
        f"- **Title:** {plan_entry.get('title', '')}\n"
        f"- **Description:** {plan_entry.get('description', '')}\n"
        f"- **Source file:** raw/{raw_filename}\n"
    )

    response = client.messages.create(
        model=writing_model,
        max_tokens=8192,
        temperature=0.3,
        system=system_prompt,
        messages=[{
            "role": "user",
            "content": user_content,
        }],
    )

    content = response.content[0].text.strip()
    # Strip markdown code block wrapper if present
    if content.startswith("```"):
        content = re.sub(r"^```(?:markdown|yaml|md)?\n?", "", content)
        content = re.sub(r"\n?```$", "", content)

    file_path = ROOT / plan_entry["path"]
    with _write_lock:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")

    return {"path": plan_entry["path"], "status": "ok"}


# ---------------------------------------------------------------------------
# Index management (append-only, machine-readable section)
# ---------------------------------------------------------------------------

def update_index_batch(all_index_entries: list[str], all_backlinks: list[dict]):
    """Update _index.md and _backlinks.md once after all compilations."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # --- _index.md ---
    index_path = WIKI_DIR / "_index.md"
    if index_path.exists():
        content = index_path.read_text(encoding="utf-8")
    else:
        content = (
            "---\ntitle: Meridian Wiki Index\ntype: index\n"
            f'created: "{now}"\nupdated: "{now}"\n---\n\n'
            "# Meridian Wiki Index\n"
        )

    # Deduplicate: don't add entries whose path already appears in the index
    new_entries = []
    for entry in all_index_entries:
        # Extract the wikilink path from the entry
        link_match = re.search(r"\[\[([^\]|]+)", entry)
        if link_match:
            link_path = link_match.group(1)
            if link_path not in content:
                new_entries.append(entry)
        else:
            new_entries.append(entry)

    if new_entries:
        # Append before the machine-readable section or at the end
        if "<!-- MACHINE_INDEX" in content:
            content = content.split("<!-- MACHINE_INDEX")[0].rstrip()
        elif "## Statistics" in content:
            content = content.split("## Statistics")[0].rstrip()

        content += "\n\n" + "\n".join(new_entries) + "\n"

    # Update/add machine-readable index
    article_count = sum(1 for _ in WIKI_DIR.rglob("*.md")
                        if _.name not in ("_index.md", "_backlinks.md", "log.md", "home.md"))
    content += (
        f"\n## Statistics\n\n"
        f"- Total articles: {article_count}\n"
        f"- Last updated: {now}\n"
    )

    # Machine-readable section
    existing_entries = []
    if "<!-- MACHINE_INDEX" in content:
        mi_match = re.search(
            r"<!-- MACHINE_INDEX -->\n```json\n(.*?)\n```",
            content, re.DOTALL
        )
        if mi_match:
            try:
                existing_entries = json.loads(mi_match.group(1))
            except json.JSONDecodeError:
                pass

    # Add new entries
    for entry_line in all_index_entries:
        link_match = re.search(r"\[\[([^\]|]+)", entry_line)
        desc_match = re.search(r"— (.+)$", entry_line)
        if link_match:
            path = link_match.group(1)
            desc = desc_match.group(1) if desc_match else ""
            if not any(e.get("path") == path for e in existing_entries):
                existing_entries.append({
                    "path": path,
                    "description": desc,
                    "added": now,
                })

    content += (
        f"\n<!-- MACHINE_INDEX -->\n"
        f"```json\n{json.dumps(existing_entries, indent=2)}\n```\n"
    )

    with _write_lock:
        index_path.write_text(content, encoding="utf-8")

    # --- _backlinks.md ---
    if all_backlinks:
        bl_path = WIKI_DIR / "_backlinks.md"
        if bl_path.exists():
            bl_content = bl_path.read_text(encoding="utf-8")
        else:
            bl_content = "---\ntitle: Backlink Registry\ntype: index\n---\n\n# Backlink Registry\n"
        bl_content = bl_content.replace("_No backlinks yet._\n", "")

        for bl in all_backlinks:
            entry = f"- [{bl['from']}]({bl['from']}) \u2192 [{bl['to']}]({bl['to']})\n"
            if entry not in bl_content:
                bl_content += entry

        bl_content = re.sub(r'updated: "\d{4}-\d{2}-\d{2}"', f'updated: "{now}"', bl_content)
        with _write_lock:
            bl_path.write_text(bl_content, encoding="utf-8")


def mark_compiled(filepath: Path):
    """Set compiled_at in the raw document's frontmatter."""
    with _write_lock:
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
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with _write_lock:
        log_path = WIKI_DIR / "log.md"
        if log_path.exists():
            content = log_path.read_text(encoding="utf-8")
        else:
            WIKI_DIR.mkdir(parents=True, exist_ok=True)
            content = "---\ntitle: Meridian Operations Log\ntype: index\n---\n\n# Operations Log\n"
        content = content.replace("_No entries yet._\n", "")
        content += f"\n## [{now}] compile | {message}\n"
        log_path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Compile one document (plan + parallel write)
# ---------------------------------------------------------------------------

def compile_one(client: anthropic.Anthropic, filepath: Path,
                index_md: str, clients_yaml: str, topics_yaml: str,
                industries_yaml: str, client_industry_map: dict[str, str],
                client_lookup: dict, topic_lookup: dict, industry_lookup: dict,
                config: dict) -> dict:
    """Plan then write all files for one raw document."""
    t0 = time.time()
    raw_content = filepath.read_text(encoding="utf-8", errors="replace")

    # Truncate for planning pass (Haiku has 200K context, need room for registries)
    planning_content = raw_content
    if len(planning_content) > 40_000:
        planning_content = planning_content[:40_000] + "\n\n[... truncated for planning ...]"

    # Keep more for writing pass (Sonnet has larger context)
    writing_content = raw_content
    if len(writing_content) > 80_000:
        writing_content = writing_content[:80_000] + "\n\n[... truncated at 80k chars ...]"

    # Pass 1: Planning (with registries)
    print(f"  Planning {filepath.name}...", file=sys.stderr)
    try:
        plan = plan_document(
            client,
            planning_content,
            index_md,
            clients_yaml,
            topics_yaml,
            industries_yaml,
            client_industry_map,
            config,
        )
    except (json.JSONDecodeError, Exception) as e:
        return {"file": str(filepath), "error": f"Planning failed: {e}"}

    # Validate plan against registries
    plan = validate_plan(plan, client_lookup, topic_lookup, industry_lookup)
    if plan.get("validation_warnings"):
        for w in plan["validation_warnings"]:
            print(f"    WARN: {w}", file=sys.stderr)

    plan_entries = plan.get("plan", [])
    if not plan_entries:
        mark_compiled(filepath)
        return {"file": str(filepath), "action": "skipped", "reason": "no valid plan entries after validation"}

    # Pass 2: Writing (parallel)
    print(f"  Writing {len(plan_entries)} files for {filepath.name}...", file=sys.stderr)
    written = []
    errors = []

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {
            pool.submit(
                write_single_file, client, writing_content, entry,
                filepath.name, config
            ): entry
            for entry in plan_entries
        }
        for future in as_completed(futures):
            entry = futures[future]
            try:
                result = future.result()
                written.append(result["path"])
            except Exception as e:
                errors.append({"path": entry["path"], "error": str(e)})

    mark_compiled(filepath)
    elapsed = time.time() - t0
    print(f"  Done {filepath.name} ({len(written)} files, {elapsed:.1f}s)", file=sys.stderr)

    return {
        "file": str(filepath),
        "action": "compiled",
        "written": written,
        "errors": errors,
        "new_clients": plan.get("new_clients", []),
        "status_changes": plan.get("status_changes", []),
        "index_entries": plan.get("index_entries", []),
        "backlinks": plan.get("backlinks", []),
        "elapsed_s": round(elapsed, 1),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Meridian Compiler")
    parser.add_argument("--file", help="Specific raw file to compile")
    parser.add_argument(
        "--limit", type=int, default=0,
        help="Max number of oldest uncompiled files to process. 0 = no cap.",
    )
    args = parser.parse_args()

    config = load_config()
    client = anthropic.Anthropic()
    index_md = load_index()

    # Load registries once — three dimensions: clients, topics, industries.
    clients_yaml = load_registry("clients.yaml")
    topics_yaml = load_registry("topics.yaml")
    industries_yaml = load_registry("industries.yaml")
    client_data = load_registry_data("clients.yaml")
    topic_data = load_registry_data("topics.yaml")
    industry_data = load_registry_data("industries.yaml")
    client_lookup = build_slug_lookup(client_data, "clients")
    topic_lookup = build_slug_lookup(topic_data, "categories")
    industry_lookup = build_slug_lookup(industry_data, "industries")
    client_industry_map = build_client_industry_map(client_data)

    print(
        f"Loaded registries: {len(client_lookup)} client aliases, "
        f"{len(topic_lookup)} topic aliases, "
        f"{len(industry_lookup)} industry aliases, "
        f"{len(client_industry_map)} client→industry mappings",
        file=sys.stderr,
    )

    if args.file:
        files = [Path(args.file)]
    else:
        files = get_uncompiled_files()
        if args.limit > 0 and len(files) > args.limit:
            print(
                f"Capping batch at {args.limit} of {len(files)} uncompiled files",
                file=sys.stderr,
            )
            files = files[:args.limit]

    if not files:
        print("No uncompiled files in raw/", file=sys.stderr)
        print(json.dumps({"status": "ok", "compiled": 0, "results": []}))
        return

    t_start = time.time()
    results = []
    all_index_entries = []
    all_backlinks = []

    # Compile documents with up to 3 concurrent
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {
            pool.submit(compile_one, client, fp, index_md,
                        clients_yaml, topics_yaml, industries_yaml,
                        client_industry_map,
                        client_lookup, topic_lookup, industry_lookup,
                        config): fp
            for fp in files
        }
        for future in as_completed(futures):
            fp = futures[future]
            try:
                result = future.result()
                results.append(result)
                all_index_entries.extend(result.get("index_entries", []))
                all_backlinks.extend(result.get("backlinks", []))
                log_paths = ", ".join(result.get("written", []))
                append_log(f'Compiled "{fp.name}" \u2192 {log_paths}')
            except Exception as e:
                results.append({"file": str(fp), "error": str(e)})

    # Batch update index and backlinks once
    if all_index_entries or all_backlinks:
        update_index_batch(all_index_entries, all_backlinks)

    elapsed_total = time.time() - t_start
    output = {
        "status": "ok",
        "compiled": len(results),
        "elapsed_s": round(elapsed_total, 1),
        "results": results,
    }
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
