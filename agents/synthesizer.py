#!/usr/bin/env python3
"""Meridian Synthesizer — produce Layer 3 synthesis articles from Layer 2 fragments.

Two-pass architecture:
  Pass 1 (Haiku): Extract claims, patterns, contradictions from batches of 20 fragments
  Pass 2 (Sonnet): Write authoritative synthesis from all extractions

Usage:
    python agents/synthesizer.py --topic google-ads
    python agents/synthesizer.py --topic google-ads --dry-run

Output: JSON summary of what was synthesized.
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import anthropic
import yaml


ROOT = Path(__file__).parent.parent
WIKI_DIR = ROOT / "wiki"
PROMPTS_DIR = ROOT / "prompts"


def load_config() -> dict:
    with open(ROOT / "config.yaml") as f:
        return yaml.safe_load(f)


def load_topics_registry() -> dict:
    """Load topics.yaml and return topic slug → metadata."""
    path = ROOT / "topics.yaml"
    if not path.exists():
        return {}
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    registry = {}
    for item in data.get("categories", []):
        slug = item.get("slug", "")
        registry[slug] = {
            "name": item.get("name", slug),
            "category": item.get("category", ""),
        }
    return registry


def get_domain_type(topic_slug: str, topics_registry: dict, config: dict) -> str:
    """Determine domain_type for a topic based on config.yaml stability profiles."""
    stability = config.get("domain_stability", {})
    for dtype, profile in stability.items():
        examples = profile.get("examples", [])
        if topic_slug in examples:
            return dtype
    # Infer from category
    category = topics_registry.get(topic_slug, {}).get("category", "")
    category_map = {
        "advertising-media": "platform-tactics",
        "seo-search": "platform-tactics",
        "content-creative": "strategy",
        "email-automation": "platform-tactics",
        "crm-sales-tools": "platform-mechanics",
        "website-web": "platform-tactics",
        "ecommerce": "platform-tactics",
        "analytics-tracking": "platform-mechanics",
        "integrations-tech": "platform-mechanics",
        "ai-automation": "platform-tactics",
        "agency-ops": "strategy",
        "sales": "strategy",
        "strategy": "strategy",
        "industry": "strategy",
        "compliance-legal": "regulatory",
        "team-ops": "strategy",
        "tools-internal": "platform-mechanics",
    }
    return category_map.get(category, "strategy")


def get_monitoring_frequency(domain_type: str, config: dict) -> str:
    """Get web_monitoring_frequency from domain stability profile."""
    stability = config.get("domain_stability", {})
    profile = stability.get(domain_type, {})
    return profile.get("web_monitoring", "quarterly")


def find_fragments(topic_slug: str) -> list[Path]:
    """Find all Layer 2 articles for a topic."""
    fragments = []

    # Check wiki/knowledge/[topic]/ directory
    topic_dir = WIKI_DIR / "knowledge" / topic_slug
    if topic_dir.exists():
        for f in topic_dir.rglob("*.md"):
            if f.name != "_index.md":
                fragments.append(f)

    return fragments


def read_fragment(path: Path) -> dict:
    """Read a fragment and return structured data."""
    content = path.read_text(encoding="utf-8", errors="replace")
    fm = {}
    body = content
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            try:
                fm = yaml.safe_load(parts[1]) or {}
            except yaml.YAMLError:
                pass
            body = parts[2].strip()

    return {
        "path": str(path.relative_to(ROOT)),
        "title": fm.get("title", path.stem),
        "created": fm.get("created", ""),
        "updated": fm.get("updated", ""),
        "client_source": fm.get("client_source", ""),
        "word_count": len(body.split()),
        "body": body,
    }


# ---------------------------------------------------------------------------
# Pass 1: Extraction (Haiku — fast, batched)
# ---------------------------------------------------------------------------

def extract_batch(client: anthropic.Anthropic, topic_name: str,
                  fragments: list[dict], config: dict) -> dict:
    """Extract insights from a batch of fragments."""
    system_prompt = (PROMPTS_DIR / "synthesizer_extract.md").read_text(encoding="utf-8")
    planning_model = config.get("compiler", {}).get(
        "planning_model", "claude-haiku-4-5-20251001"
    )

    # Build fragment content
    frag_text = "\n\n".join(
        f"### {f['path']}\n**Title:** {f['title']}\n"
        f"**Client:** {f.get('client_source', '-')}\n\n"
        f"{f['body'][:3000]}"
        for f in fragments
    )

    response = client.messages.create(
        model=planning_model,
        max_tokens=4096,
        temperature=0.2,
        system=system_prompt,
        messages=[{
            "role": "user",
            "content": f"## Topic: {topic_name}\n\n## Fragments\n\n{frag_text}",
        }],
    )

    text = response.content[0].text
    json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if json_match:
        text = json_match.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"claims": [], "patterns": [], "contradictions": [],
                "exceptions": [], "evidence": [], "client_mentions": []}


def merge_extractions(extractions: list[dict]) -> dict:
    """Merge multiple batch extractions into one."""
    merged = {
        "claims": [],
        "patterns": [],
        "contradictions": [],
        "exceptions": [],
        "evidence": [],
        "client_mentions": set(),
    }
    for ext in extractions:
        merged["claims"].extend(ext.get("claims", []))
        merged["patterns"].extend(ext.get("patterns", []))
        merged["contradictions"].extend(ext.get("contradictions", []))
        merged["exceptions"].extend(ext.get("exceptions", []))
        merged["evidence"].extend(ext.get("evidence", []))
        for c in ext.get("client_mentions", []):
            merged["client_mentions"].add(c)

    merged["client_mentions"] = sorted(merged["client_mentions"])
    return merged


# ---------------------------------------------------------------------------
# Pass 2: Writing (Sonnet — quality)
# ---------------------------------------------------------------------------

def write_synthesis(client: anthropic.Anthropic, topic_name: str, topic_slug: str,
                    extractions: dict, fragment_count: int, earliest_date: str,
                    latest_date: str, domain_type: str, monitoring_freq: str,
                    config: dict) -> str:
    """Write the Layer 3 synthesis article."""
    system_prompt = (PROMPTS_DIR / "synthesizer_write.md").read_text(encoding="utf-8")
    writing_model = config.get("compiler", {}).get(
        "writing_model", "claude-sonnet-4-6"
    )

    # Build extraction summary for the LLM
    ext_json = json.dumps(extractions, indent=2, default=str)
    if len(ext_json) > 60000:
        ext_json = ext_json[:60000] + "\n... [truncated]"

    user_content = (
        f"## Topic: {topic_name}\n"
        f"## Topic slug: {topic_slug}\n"
        f"## Domain type: {domain_type}\n"
        f"## Fragment count: {fragment_count}\n"
        f"## Date range: {earliest_date} to {latest_date}\n"
        f"## Monitoring frequency: {monitoring_freq}\n"
        f"## Clients mentioned: {', '.join(extractions.get('client_mentions', []))}\n\n"
        f"## Extractions\n\n{ext_json}"
    )

    response = client.messages.create(
        model=writing_model,
        max_tokens=16384,
        temperature=0.3,
        system=system_prompt,
        messages=[{
            "role": "user",
            "content": user_content,
        }],
    )

    content = response.content[0].text.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:markdown|yaml|md)?\n?", "", content)
        content = re.sub(r"\n?```$", "", content)
    return content


# ---------------------------------------------------------------------------
# Main synthesis flow
# ---------------------------------------------------------------------------

def synthesize_topic(topic_slug: str, dry_run: bool = False, force: bool = False) -> dict:
    """Synthesize one topic into a Layer 3 article."""
    t0 = time.time()
    config = load_config()
    topics_registry = load_topics_registry()
    topic_info = topics_registry.get(topic_slug, {"name": topic_slug})
    topic_name = topic_info.get("name", topic_slug)

    # Check if already synthesized (unless --force)
    output_path = WIKI_DIR / "knowledge" / topic_slug / "index.md"
    if output_path.exists() and not force:
        content = output_path.read_text(encoding="utf-8", errors="replace")
        if "layer: 3" in content:
            return {"topic": topic_slug, "action": "skipped", "reason": "already synthesized (use --force to overwrite)"}

    # Find fragments
    fragment_paths = find_fragments(topic_slug)
    if not fragment_paths:
        return {"topic": topic_slug, "error": "no fragments found", "fragment_count": 0}

    print(f"Found {len(fragment_paths)} fragments for '{topic_name}'", file=sys.stderr)

    # Read all fragments
    fragments = []
    for fp in fragment_paths:
        try:
            fragments.append(read_fragment(fp))
        except Exception as e:
            print(f"  Error reading {fp}: {e}", file=sys.stderr)

    if not fragments:
        return {"topic": topic_slug, "error": "no readable fragments", "fragment_count": 0}

    # Get date range
    dates = [f["created"] for f in fragments if f.get("created")]
    earliest = min(dates) if dates else "unknown"
    latest = max(dates) if dates else "unknown"

    if dry_run:
        return {
            "topic": topic_slug,
            "topic_name": topic_name,
            "fragment_count": len(fragments),
            "date_range": f"{earliest} to {latest}",
            "action": "dry_run",
        }

    # Domain metadata
    domain_type = get_domain_type(topic_slug, topics_registry, config)
    monitoring_freq = get_monitoring_frequency(domain_type, config)

    # Pass 1: Extract in batches
    api_client = anthropic.Anthropic()
    batch_size = 20
    extractions = []

    for i in range(0, len(fragments), batch_size):
        batch = fragments[i:i + batch_size]
        batch_num = (i // batch_size) + 1
        total_batches = (len(fragments) + batch_size - 1) // batch_size
        print(f"  Extracting batch {batch_num}/{total_batches} ({len(batch)} fragments)...",
              file=sys.stderr)
        ext = extract_batch(api_client, topic_name, batch, config)
        extractions.append(ext)
        time.sleep(0.5)

    merged = merge_extractions(extractions)
    evidence_count = len(merged["claims"]) + len(merged["evidence"])

    print(f"  Extracted: {len(merged['claims'])} claims, {len(merged['patterns'])} patterns, "
          f"{len(merged['contradictions'])} contradictions, "
          f"{len(merged['client_mentions'])} clients", file=sys.stderr)

    # Pass 2: Write synthesis
    print(f"  Writing synthesis...", file=sys.stderr)
    synthesis_content = write_synthesis(
        api_client, topic_name, topic_slug, merged,
        len(fragments), earliest, latest, domain_type,
        monitoring_freq, config
    )

    # Write to wiki/knowledge/[topic]/index.md
    output_path = WIKI_DIR / "knowledge" / topic_slug / "index.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(synthesis_content, encoding="utf-8")

    # Append to log
    log_path = WIKI_DIR / "log.md"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if log_path.exists():
        log_content = log_path.read_text(encoding="utf-8")
    else:
        log_content = ""
    log_content += (
        f"\n## [{now}] synthesize | Layer 3: {topic_name} "
        f"({len(fragments)} fragments, {evidence_count} evidence points)\n"
    )
    log_path.write_text(log_content, encoding="utf-8")

    elapsed = time.time() - t0
    print(f"  Done '{topic_name}' ({elapsed:.1f}s)", file=sys.stderr)

    return {
        "topic": topic_slug,
        "topic_name": topic_name,
        "fragment_count": len(fragments),
        "evidence_count": evidence_count,
        "claims": len(merged["claims"]),
        "patterns": len(merged["patterns"]),
        "contradictions": len(merged["contradictions"]),
        "clients_mentioned": merged["client_mentions"],
        "output_path": str(output_path.relative_to(ROOT)),
        "elapsed_s": round(elapsed, 1),
    }


def main():
    parser = argparse.ArgumentParser(description="Meridian Synthesizer")
    parser.add_argument("--topic", required=True, help="Topic slug to synthesize")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be synthesized")
    parser.add_argument("--force", action="store_true", help="Overwrite existing synthesis")
    args = parser.parse_args()

    result = synthesize_topic(args.topic, dry_run=args.dry_run, force=args.force)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
