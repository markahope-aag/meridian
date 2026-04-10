#!/usr/bin/env python3
"""Meridian Synthesizer — produce Layer 3 synthesis articles from Layer 2 fragments.

Two-pass architecture:
  Pass 1 (Haiku): Extract claims, patterns, contradictions from batches of 20 fragments
  Pass 2 (Sonnet): Write authoritative synthesis from all extractions

Extraction output is cached to disk so Pass 2 can be re-run cheaply when
only the write prompt changes. Output is versioned so prior renders are
recoverable without restic.

Usage:
    # Full pipeline (extract + write) — same as the old CLI
    python agents/synthesizer.py run --topic google-ads
    python agents/synthesizer.py --topic google-ads          # legacy alias of `run`

    # Extract only (runs Pass 1, writes to cache, skips Pass 2)
    python agents/synthesizer.py extract --topic google-ads

    # Write only (reads from cache, runs Pass 2) — fails if cache missing
    python agents/synthesizer.py write --topic google-ads

    # Force cache invalidation
    python agents/synthesizer.py extract --topic google-ads --re-extract
    python agents/synthesizer.py run --topic google-ads --re-extract

    # Show the plan without calling any LLM
    python agents/synthesizer.py run --topic google-ads --dry-run

Output: JSON summary of what was synthesized.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import anthropic
import yaml


ROOT = Path(__file__).parent.parent
WIKI_DIR = ROOT / "wiki"
PROMPTS_DIR = ROOT / "prompts"
CACHE_DIR = ROOT / "cache" / "extractions"
VERSIONS_DIR = ROOT / "state" / "synthesis_versions"

EXTRACT_CACHE_SCHEMA_VERSION = 1


def _sha12(path: Path) -> str:
    """Return a short SHA-256 hash of a file's contents for provenance stamping."""
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_stamp() -> str:
    """Filesystem-safe timestamp for versioned filenames."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


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
        max_tokens=16384,
        temperature=0.2,
        system=system_prompt,
        messages=[{
            "role": "user",
            "content": f"## Topic: {topic_name}\n\n## Fragments\n\n{frag_text}",
        }],
    )

    text = response.content[0].text

    # Strip markdown code block wrapper if present
    stripped = text.strip()
    if stripped.startswith("```"):
        # Remove opening ```json and closing ```
        stripped = re.sub(r"^```(?:json)?\s*\n?", "", stripped)
        stripped = re.sub(r"\n?```\s*$", "", stripped)

    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        # Try to find the outermost JSON object
        start = stripped.find("{")
        if start >= 0:
            # Find matching closing brace by counting
            depth = 0
            for i in range(start, len(stripped)):
                if stripped[i] == "{":
                    depth += 1
                elif stripped[i] == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(stripped[start:i + 1])
                        except json.JSONDecodeError:
                            break

        print(f"    WARN: failed to parse extraction JSON ({len(text)} chars)", file=sys.stderr)
        print(f"    First 200 chars: {text[:200]}", file=sys.stderr)
        return {"claims": [], "patterns": [], "contradictions": [],
                "exceptions": [], "evidence": [], "client_mentions": []}


def _cache_path(topic_slug: str) -> Path:
    return CACHE_DIR / f"{topic_slug}.json"


def load_extraction_cache(topic_slug: str, fragment_paths: list[Path]) -> dict | None:
    """Return cached extraction data if it is still valid, otherwise None.

    Invalidation rules:
      - file missing
      - cache schema version mismatch
      - extract prompt hash differs from current prompt
      - any fragment mtime is newer than the cache
    """
    path = _cache_path(topic_slug)
    if not path.exists():
        return None
    try:
        cache = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    if cache.get("schema_version") != EXTRACT_CACHE_SCHEMA_VERSION:
        return None

    current_prompt_sha = _sha12(PROMPTS_DIR / "synthesizer_extract.md")
    if cache.get("extract_prompt_sha") != current_prompt_sha:
        return None

    cache_mtime = path.stat().st_mtime
    for fp in fragment_paths:
        try:
            if fp.stat().st_mtime > cache_mtime:
                return None
        except OSError:
            return None

    return cache


def save_extraction_cache(
    topic_slug: str,
    topic_name: str,
    merged: dict,
    fragment_paths: list[Path],
    extract_model: str,
) -> Path:
    """Persist merged extraction data so the write pass can re-run cheaply."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(topic_slug)
    newest_mtime = max(
        (fp.stat().st_mtime for fp in fragment_paths if fp.exists()),
        default=0.0,
    )
    payload = {
        "schema_version": EXTRACT_CACHE_SCHEMA_VERSION,
        "topic_slug": topic_slug,
        "topic_name": topic_name,
        "extracted_at": _now_iso(),
        "extract_prompt_sha": _sha12(PROMPTS_DIR / "synthesizer_extract.md"),
        "extract_model": extract_model,
        "fragment_count": len(fragment_paths),
        "newest_fragment_mtime": newest_mtime,
        **merged,
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)
    return path


def archive_existing_synthesis(topic_slug: str) -> Path | None:
    """Move a pre-existing index.md to the versions archive before we overwrite.

    Returns the path the old file was archived to, or None if there was
    nothing to archive. Keeps a full history of prior renders so a bad
    prompt iteration can be rolled back without restic.
    """
    output_path = WIKI_DIR / "knowledge" / topic_slug / "index.md"
    if not output_path.exists():
        return None
    VERSIONS_DIR.mkdir(parents=True, exist_ok=True)
    topic_versions = VERSIONS_DIR / topic_slug
    topic_versions.mkdir(parents=True, exist_ok=True)
    archived = topic_versions / f"{_now_stamp()}.md"
    shutil.copy2(output_path, archived)
    return archived


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

# ---------------------------------------------------------------------------
# Topic loading — shared by extract and write passes
# ---------------------------------------------------------------------------

def _load_topic_bundle(topic_slug: str) -> dict | None:
    """Read fragments + metadata for a topic. Returns None if nothing to do."""
    config = load_config()
    topics_registry = load_topics_registry()
    topic_info = topics_registry.get(topic_slug, {"name": topic_slug})
    topic_name = topic_info.get("name", topic_slug)

    fragment_paths = find_fragments(topic_slug)
    if not fragment_paths:
        return None

    fragments = []
    for fp in fragment_paths:
        try:
            fragments.append(read_fragment(fp))
        except Exception as e:
            print(f"  Error reading {fp}: {e}", file=sys.stderr)

    if not fragments:
        return None

    dates = [f["created"] for f in fragments if f.get("created")]
    earliest = min(dates) if dates else "unknown"
    latest = max(dates) if dates else "unknown"

    domain_type = get_domain_type(topic_slug, topics_registry, config)
    monitoring_freq = get_monitoring_frequency(domain_type, config)

    return {
        "config": config,
        "topic_name": topic_name,
        "fragments": fragments,
        "fragment_paths": fragment_paths,
        "earliest": earliest,
        "latest": latest,
        "domain_type": domain_type,
        "monitoring_freq": monitoring_freq,
    }


# ---------------------------------------------------------------------------
# Pass 1 runner
# ---------------------------------------------------------------------------

def do_extract(topic_slug: str, re_extract: bool = False) -> dict:
    """Run extraction (Pass 1). Writes to the extraction cache.

    Returns a summary dict containing either the reused cache metadata
    or the stats of the extraction that was just performed.
    """
    t0 = time.time()
    bundle = _load_topic_bundle(topic_slug)
    if bundle is None:
        return {"topic": topic_slug, "error": "no fragments found", "fragment_count": 0}

    topic_name = bundle["topic_name"]
    fragment_paths = bundle["fragment_paths"]
    fragments = bundle["fragments"]
    config = bundle["config"]

    if not re_extract:
        cached = load_extraction_cache(topic_slug, fragment_paths)
        if cached is not None:
            print(
                f"  Extraction cache hit for '{topic_name}' "
                f"({cached.get('fragment_count', '?')} fragments)",
                file=sys.stderr,
            )
            return {
                "topic": topic_slug,
                "topic_name": topic_name,
                "action": "cache_hit",
                "fragment_count": cached.get("fragment_count", len(fragment_paths)),
                "claims": len(cached.get("claims", [])),
                "patterns": len(cached.get("patterns", [])),
                "elapsed_s": round(time.time() - t0, 1),
            }

    api_client = anthropic.Anthropic()
    extract_model = config.get("compiler", {}).get(
        "planning_model", "claude-haiku-4-5-20251001"
    )
    batch_size = 20
    extractions = []

    print(f"Found {len(fragment_paths)} fragments for '{topic_name}'", file=sys.stderr)
    for i in range(0, len(fragments), batch_size):
        batch = fragments[i:i + batch_size]
        batch_num = (i // batch_size) + 1
        total_batches = (len(fragments) + batch_size - 1) // batch_size
        print(
            f"  Extracting batch {batch_num}/{total_batches} ({len(batch)} fragments)...",
            file=sys.stderr,
        )
        ext = extract_batch(api_client, topic_name, batch, config)
        extractions.append(ext)
        time.sleep(0.5)

    merged = merge_extractions(extractions)
    save_extraction_cache(topic_slug, topic_name, merged, fragment_paths, extract_model)

    print(
        f"  Cached: {len(merged['claims'])} claims, {len(merged['patterns'])} patterns, "
        f"{len(merged['contradictions'])} contradictions, "
        f"{len(merged['client_mentions'])} clients",
        file=sys.stderr,
    )

    return {
        "topic": topic_slug,
        "topic_name": topic_name,
        "action": "extracted",
        "fragment_count": len(fragment_paths),
        "claims": len(merged["claims"]),
        "patterns": len(merged["patterns"]),
        "contradictions": len(merged["contradictions"]),
        "clients_mentioned": merged["client_mentions"],
        "elapsed_s": round(time.time() - t0, 1),
    }


# ---------------------------------------------------------------------------
# Pass 2 runner
# ---------------------------------------------------------------------------

def _inject_provenance(content: str, provenance: dict) -> str:
    """Insert Meridian provenance fields into the frontmatter of a synthesis.

    The writer LLM is instructed to produce complete frontmatter, but the
    run-specific fields (run_id, prompt hashes, models, cache state) are
    authored by this runner — they're not something the LLM should guess.
    """
    if not content.startswith("---"):
        return content
    parts = content.split("---", 2)
    if len(parts) < 3:
        return content

    prov_lines = [f"{k}: {json.dumps(v)}" for k, v in provenance.items()]
    prov_block = "\n" + "\n".join(prov_lines) + "\n"
    parts[1] = parts[1].rstrip() + prov_block
    return "---".join(parts)


def do_write(
    topic_slug: str,
    fixture_path: Path | None = None,
    output_path_override: Path | None = None,
) -> dict:
    """Run the write pass (Pass 2). Requires a valid extraction cache.

    Archives any existing index.md to state/synthesis_versions/<slug>/
    before overwriting. Stamps Meridian provenance fields into the
    frontmatter of the new file.

    Test-harness parameters (normally None for production runs):
      fixture_path: read extraction JSON from this path instead of the
        cache. When set, no cache validation is performed and the
        extraction is not modified. Used by the regression harness to
        run the write pass against frozen inputs.
      output_path_override: write the synthesis to this path instead of
        wiki/knowledge/<slug>/index.md. Skips the archive step. Used
        by the regression harness to capture outputs without touching
        production state.
    """
    t0 = time.time()
    bundle = _load_topic_bundle(topic_slug)
    if bundle is None:
        return {"topic": topic_slug, "error": "no fragments found", "fragment_count": 0}

    topic_name = bundle["topic_name"]
    fragment_paths = bundle["fragment_paths"]
    fragments = bundle["fragments"]
    config = bundle["config"]

    if fixture_path is not None:
        # Test-harness mode: read the frozen extraction from disk. Do not
        # validate, do not touch the production cache.
        if not fixture_path.exists():
            return {"topic": topic_slug, "error": f"fixture not found: {fixture_path}"}
        try:
            cached = json.loads(fixture_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            return {"topic": topic_slug, "error": f"failed to read fixture: {e}"}
        cache_hit = True
        print(
            f"  Using fixture for '{topic_name}' ({cached.get('fragment_count', '?')} fragments)",
            file=sys.stderr,
        )
    else:
        cached = load_extraction_cache(topic_slug, fragment_paths)
        cache_hit = cached is not None
        if cached is None:
            # No cache — fall back to running extraction inline so write can proceed.
            print(
                f"  No extraction cache for '{topic_name}' — running extraction first",
                file=sys.stderr,
            )
            extract_result = do_extract(topic_slug, re_extract=True)
            if extract_result.get("error"):
                return extract_result
            cached = load_extraction_cache(topic_slug, fragment_paths)
            if cached is None:
                return {"topic": topic_slug, "error": "extraction cache still missing after rebuild"}

    merged = {
        "claims": cached.get("claims", []),
        "patterns": cached.get("patterns", []),
        "contradictions": cached.get("contradictions", []),
        "exceptions": cached.get("exceptions", []),
        "evidence": cached.get("evidence", []),
        "client_mentions": cached.get("client_mentions", []),
    }
    evidence_count = len(merged["claims"]) + len(merged["evidence"])

    api_client = anthropic.Anthropic()
    writer_model = config.get("compiler", {}).get("writing_model", "claude-sonnet-4-6")
    extract_model = cached.get("extract_model") or config.get("compiler", {}).get(
        "planning_model", "claude-haiku-4-5-20251001"
    )

    print("  Writing synthesis...", file=sys.stderr)
    synthesis_content = write_synthesis(
        api_client,
        topic_name,
        topic_slug,
        merged,
        len(fragments),
        bundle["earliest"],
        bundle["latest"],
        bundle["domain_type"],
        bundle["monitoring_freq"],
        config,
    )

    # Stamp provenance so we can always answer "which prompt / model /
    # cache state produced this file".
    run_id = uuid.uuid4().hex[:12]
    provenance = {
        "generated_at": _now_iso(),
        "run_id": run_id,
        "synthesizer_prompt_sha": _sha12(PROMPTS_DIR / "synthesizer_write.md"),
        "extract_prompt_sha": _sha12(PROMPTS_DIR / "synthesizer_extract.md"),
        "writer_model": writer_model,
        "extract_model": extract_model,
        "extraction_cache_hit": bool(cache_hit),
    }
    synthesis_content = _inject_provenance(synthesis_content, provenance)

    # Test-harness mode short-circuits archiving and log updates: the
    # harness writes to a scratch directory and must not mutate
    # production state.
    if output_path_override is not None:
        output_path_override.parent.mkdir(parents=True, exist_ok=True)
        output_path_override.write_text(synthesis_content, encoding="utf-8")
        elapsed = time.time() - t0
        print(f"  Done '{topic_name}' fixture run ({elapsed:.1f}s)", file=sys.stderr)
        return {
            "topic": topic_slug,
            "topic_name": topic_name,
            "action": "fixture_written",
            "run_id": run_id,
            "fragment_count": len(fragments),
            "output_path": str(output_path_override),
            "provenance": provenance,
            "elapsed_s": round(elapsed, 1),
        }

    # Archive the previous version before overwriting, so rollbacks and
    # prompt A/B comparisons are one mv away.
    archived_path = archive_existing_synthesis(topic_slug)

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
    cache_marker = "cached" if cache_hit else "fresh"
    log_content += (
        f"\n## [{now}] synthesize | Layer 3: {topic_name} "
        f"({len(fragments)} fragments, {evidence_count} evidence points, {cache_marker}, run {run_id})\n"
    )
    log_path.write_text(log_content, encoding="utf-8")

    elapsed = time.time() - t0
    print(f"  Done '{topic_name}' ({elapsed:.1f}s, run {run_id})", file=sys.stderr)

    return {
        "topic": topic_slug,
        "topic_name": topic_name,
        "action": "written",
        "run_id": run_id,
        "cache_hit": cache_hit,
        "fragment_count": len(fragments),
        "evidence_count": evidence_count,
        "claims": len(merged["claims"]),
        "patterns": len(merged["patterns"]),
        "contradictions": len(merged["contradictions"]),
        "clients_mentioned": merged["client_mentions"],
        "output_path": str(output_path.relative_to(ROOT)),
        "archived_to": str(archived_path.relative_to(ROOT)) if archived_path else None,
        "provenance": provenance,
        "elapsed_s": round(elapsed, 1),
    }


# ---------------------------------------------------------------------------
# Combined runner — extract then write (default CLI behaviour, legacy API)
# ---------------------------------------------------------------------------

def synthesize_topic(
    topic_slug: str,
    dry_run: bool = False,
    force: bool = False,
    re_extract: bool = False,
) -> dict:
    """Extract + write. Kept as the public entry point for the scheduler."""
    if dry_run:
        bundle = _load_topic_bundle(topic_slug)
        if bundle is None:
            return {"topic": topic_slug, "error": "no fragments found", "fragment_count": 0}
        return {
            "topic": topic_slug,
            "topic_name": bundle["topic_name"],
            "fragment_count": len(bundle["fragments"]),
            "date_range": f"{bundle['earliest']} to {bundle['latest']}",
            "action": "dry_run",
        }

    # Respect an existing synthesis unless force is set. Matches the old API.
    output_path = WIKI_DIR / "knowledge" / topic_slug / "index.md"
    if output_path.exists() and not force:
        content = output_path.read_text(encoding="utf-8", errors="replace")
        if "layer: 3" in content:
            return {
                "topic": topic_slug,
                "action": "skipped",
                "reason": "already synthesized (use --force to overwrite)",
            }

    extract_result = do_extract(topic_slug, re_extract=re_extract)
    if extract_result.get("error"):
        return extract_result
    return do_write(topic_slug)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Meridian Synthesizer")
    # Legacy top-level flags so existing callers keep working.
    parser.add_argument("--topic", help="Topic slug (legacy; equivalent to `run --topic`)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--re-extract", action="store_true")

    sub = parser.add_subparsers(dest="command")

    p_extract = sub.add_parser("extract", help="Run extraction only — writes to cache")
    p_extract.add_argument("--topic", required=True)
    p_extract.add_argument("--re-extract", action="store_true",
                           help="Ignore existing cache and re-extract")

    p_write = sub.add_parser("write", help="Run write pass only — requires cached extraction")
    p_write.add_argument("--topic", required=True)
    p_write.add_argument(
        "--fixture",
        help="Path to a frozen extraction JSON. When set, bypasses the cache "
             "and does not modify production state. Used by the test harness.",
    )
    p_write.add_argument(
        "--output",
        help="Write synthesis to this path instead of wiki/knowledge/<slug>/index.md. "
             "Required when --fixture is set; skips archive + log update.",
    )

    p_run = sub.add_parser("run", help="Extract (or reuse cache) then write")
    p_run.add_argument("--topic", required=True)
    p_run.add_argument("--force", action="store_true",
                       help="Overwrite existing synthesis")
    p_run.add_argument("--re-extract", action="store_true",
                       help="Ignore cache and re-run extraction before writing")
    p_run.add_argument("--dry-run", action="store_true")

    return parser


def main():
    parser = _build_parser()
    args = parser.parse_args()

    # Legacy form: `synthesizer.py --topic X` with no subcommand.
    if args.command is None:
        if not args.topic:
            parser.error("either a subcommand or --topic is required")
        result = synthesize_topic(
            args.topic,
            dry_run=args.dry_run,
            force=args.force,
            re_extract=args.re_extract,
        )
    elif args.command == "extract":
        result = do_extract(args.topic, re_extract=args.re_extract)
    elif args.command == "write":
        fixture = Path(args.fixture) if getattr(args, "fixture", None) else None
        output = Path(args.output) if getattr(args, "output", None) else None
        if fixture is not None and output is None:
            parser.error("--output is required when --fixture is set")
        result = do_write(args.topic, fixture_path=fixture, output_path_override=output)
    elif args.command == "run":
        result = synthesize_topic(
            args.topic,
            dry_run=args.dry_run,
            force=args.force,
            re_extract=args.re_extract,
        )
    else:
        parser.error(f"unknown command: {args.command}")
        return

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
