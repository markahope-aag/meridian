#!/usr/bin/env python3
"""Meridian Linter — wiki health checks, auto-fix, and flagging.

Usage:
    python agents/linter.py                          # full lint, auto-fix enabled
    python agents/linter.py --dry-run                # report only, no changes
    python agents/linter.py --scope contradictions   # specific check only
    python agents/linter.py --scope orphans
    python agents/linter.py --scope gaps
    python agents/linter.py --scope all

Output: JSON with actions taken, flags, and summary.
"""

import argparse
import json
import os
import re
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

import anthropic
import yaml


ROOT = Path(__file__).parent.parent
WIKI_DIR = ROOT / "wiki"
CAPTURE_DIR = ROOT / "capture"
OUTPUTS_DIR = ROOT / "outputs"
PROMPTS_DIR = ROOT / "prompts"

# Hard caps to prevent the linter from doing damage on a corpus it
# doesn't fully understand. These are belt-and-suspenders defenses
# alongside the prompt-level rules in prompts/linter.md.
MAX_INDEX_AUTO_ADDS = 50      # never dump more than 50 new entries into _index.md in one run
MAX_AUTO_STUBS = 20           # never auto-create more than 20 stub files in one run

_write_lock = threading.Lock()


def load_config() -> dict:
    with open(ROOT / "config.yaml") as f:
        return yaml.safe_load(f)


def load_prompt() -> str:
    return (PROMPTS_DIR / "linter.md").read_text(encoding="utf-8")


def load_index() -> str:
    path = WIKI_DIR / "_index.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def load_backlinks() -> str:
    path = WIKI_DIR / "_backlinks.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""


# ---------------------------------------------------------------------------
# Registry loading — used for stub location validation
# ---------------------------------------------------------------------------

def _load_registry_slugs(filename: str) -> set[str]:
    """Return the set of canonical slugs in a registry yaml.

    Aliases are NOT included — only canonical slugs. The compiler does
    alias matching at filing time; the linter only needs to know what
    paths are valid for stub creation.
    """
    path = ROOT / filename
    if not path.exists():
        return set()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return set()
    items = (
        data.get("clients")
        or data.get("categories")
        or data.get("industries")
        or data.get("topics")
        or []
    )
    return {item["slug"] for item in items if isinstance(item, dict) and item.get("slug")}


def _load_non_synthesizable_slugs(filename: str) -> set[str]:
    """Return the subset of slugs in a registry flagged `synthesize: false`.

    These topics (like engineering `unclassified`) are canonical and
    registered, but deliberately never synthesized. The linter must
    recognize them so it doesn't flag them as "thin" or "needs synthesis."
    """
    path = ROOT / filename
    if not path.exists():
        return set()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return set()
    items = (
        data.get("clients")
        or data.get("categories")
        or data.get("industries")
        or data.get("topics")
        or []
    )
    return {
        item["slug"]
        for item in items
        if isinstance(item, dict) and item.get("slug") and item.get("synthesize") is False
    }


def load_all_registries() -> dict[str, set[str]]:
    """Return {dimension: set_of_canonical_slugs} for every taxonomy.

    After the April rebuild and the engineering + interests namespace
    work, there are five registered dimensions: clients, topics,
    industries, engineering-topics, interests-topics.
    """
    return {
        "clients": _load_registry_slugs("clients.yaml"),
        "topics": _load_registry_slugs("topics.yaml"),
        "industries": _load_registry_slugs("industries.yaml"),
        "engineering-topics": _load_registry_slugs("engineering-topics.yaml"),
        "interests-topics": _load_registry_slugs("interests-topics.yaml"),
    }


def format_registry_slugs(registries: dict[str, set[str]]) -> str:
    """Compact representation of all registries for the LLM prompt."""
    parts = []
    for label, slugs in (
        ("clients", registries.get("clients", set())),
        ("topics", registries.get("topics", set())),
        ("industries", registries.get("industries", set())),
        ("engineering-topics", registries.get("engineering-topics", set())),
        ("interests-topics", registries.get("interests-topics", set())),
    ):
        if not slugs:
            continue
        sorted_slugs = ", ".join(sorted(slugs))
        parts.append(f"### {label}.yaml\n{sorted_slugs}")
    return "\n\n".join(parts) if parts else "(no registries available)"


# ---------------------------------------------------------------------------
# Article loading with dimensional sampling
# ---------------------------------------------------------------------------

def _classify_path(rel_path: str) -> str:
    """Bucket a wiki path into a dimension label for proportional sampling.

    Five content namespaces + four flat areas. Proportional sampling
    gives each bucket equal char budget, so engineering gets the same
    attention as knowledge even though the file counts differ.
    """
    if rel_path.startswith("wiki/clients/"):
        return "clients"
    if rel_path.startswith("wiki/knowledge/"):
        return "knowledge"
    if rel_path.startswith("wiki/industries/"):
        return "industries"
    if rel_path.startswith("wiki/engineering/"):
        return "engineering"
    if rel_path.startswith("wiki/interests/"):
        return "interests"
    if rel_path.startswith("wiki/concepts/"):
        return "concepts"
    if rel_path.startswith("wiki/articles/"):
        return "articles"
    return "other"


def load_all_articles() -> dict[str, str]:
    """Load all wiki articles as {relative_path: content}.

    Used by find_missing_index_entries and rebuild_backlinks, both of
    which need the full set, not a sample. The LLM analysis path uses
    a sampled subset via load_articles_sampled.
    """
    articles = {}
    if not WIKI_DIR.exists():
        return articles
    for md_file in WIKI_DIR.rglob("*.md"):
        if md_file.name in ("home.md", "PLACEHOLDER.md"):
            continue
        try:
            rel = str(md_file.relative_to(ROOT))
            articles[rel] = md_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
    return articles


def load_articles_sampled(char_budget: int = 150_000) -> dict[str, str]:
    """Return a representative sample of wiki articles, allocating the
    char budget proportionally across dimensions.

    The previous implementation walked alphabetically and stopped at the
    cap, which biased the sample heavily toward dimensions whose names
    sort early (clients > industries > knowledge). With ~3000 wiki files
    that meant the LLM only ever saw the first ~10% of content. This
    spreads the budget across dimensions so industries and knowledge
    actually get representation.
    """
    all_files = load_all_articles()
    by_dim: dict[str, list[tuple[str, str]]] = {}
    for path, content in all_files.items():
        dim = _classify_path(path)
        by_dim.setdefault(dim, []).append((path, content))

    if not by_dim:
        return {}

    dimensions = sorted(by_dim.keys())
    per_dim_budget = char_budget // len(dimensions)

    sampled: dict[str, str] = {}
    for dim in dimensions:
        files = sorted(by_dim[dim])
        spent = 0
        for path, content in files:
            if spent >= per_dim_budget:
                break
            # Per-file cap: don't let one giant file eat the dimension's budget
            snippet = content[:8000]
            sampled[path] = snippet
            spent += len(snippet)
    return sampled


def now_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# LLM analysis
# ---------------------------------------------------------------------------

def run_llm_analysis(client: anthropic.Anthropic, index_md: str,
                     backlinks_md: str, articles: dict[str, str],
                     registries: dict[str, set[str]],
                     config: dict, scope: str) -> dict:
    """Send wiki content to LLM for analysis."""
    system_prompt = load_prompt()

    # Articles arrive pre-sampled by load_articles_sampled (proportional
    # across dimensions). Just dump them with their dimension prefix.
    article_sections = []
    for path, content in sorted(articles.items()):
        article_sections.append(f"\n### {path}\n\n{content}")

    scope_instruction = ""
    if scope != "all":
        scope_instruction = f"\n\nFocus ONLY on: {scope}. Return empty arrays for other categories."

    registry_block = format_registry_slugs(registries)

    user_content = (
        f"## wiki/_index.md\n\n{index_md}\n\n"
        f"## wiki/_backlinks.md\n\n{backlinks_md}\n\n"
        f"## Taxonomy registries (for stub location validation)\n\n{registry_block}\n\n"
        f"## Wiki Articles (proportional sample across dimensions)\n\n{''.join(article_sections)}"
        f"{scope_instruction}"
    )

    response = client.messages.create(
        model=config["llm"]["model"],
        max_tokens=8192,
        temperature=0.2,
        system=system_prompt,
        messages=[{"role": "user", "content": user_content}],
    )

    text = response.content[0].text
    json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if json_match:
        text = json_match.group(1)
    return json.loads(text)


# ---------------------------------------------------------------------------
# Stub location validation — registry-enforced
# ---------------------------------------------------------------------------

def validate_stub_location(location: str, registries: dict[str, set[str]]) -> tuple[bool, str]:
    """Decide whether a suggested stub location is safe to create.

    Returns (allowed, reason). Free-form areas (concepts/, articles/)
    are always allowed. Registry-bound areas (clients/, knowledge/,
    industries/) require the slug component of the path to exist in
    the matching registry. Anything else is rejected.

    Examples:
        wiki/concepts/foo.md                 -> allowed (free-form)
        wiki/articles/foo.md                 -> allowed (free-form)
        wiki/knowledge/seo/foo.md            -> allowed if 'seo' in topics.yaml
        wiki/knowledge/blockchain/foo.md     -> rejected (not in topics)
        wiki/industries/healthcare/foo.md    -> allowed if 'healthcare' in industries.yaml
        wiki/clients/current/aviary/foo.md   -> allowed if 'aviary' in clients.yaml
        wiki/random/foo.md                   -> rejected (unknown area)
    """
    if not location:
        return False, "empty location"
    parts = location.strip().lstrip("/").split("/")
    if len(parts) < 2 or parts[0] != "wiki":
        return False, f"location must start with wiki/ (got {location})"
    area = parts[1]
    if area in ("concepts", "articles"):
        return True, "free-form area"
    if area == "knowledge":
        if len(parts) < 3:
            return False, "missing topic slug"
        slug = parts[2]
        if slug in registries.get("topics", set()):
            return True, f"topic '{slug}' in registry"
        return False, f"topic '{slug}' not in topics.yaml"
    if area == "industries":
        if len(parts) < 3:
            return False, "missing industry slug"
        slug = parts[2]
        if slug in registries.get("industries", set()):
            return True, f"industry '{slug}' in registry"
        return False, f"industry '{slug}' not in industries.yaml"
    if area == "engineering":
        if len(parts) < 3:
            return False, "missing engineering topic slug"
        slug = parts[2]
        if slug in registries.get("engineering-topics", set()):
            return True, f"engineering topic '{slug}' in registry"
        return False, f"engineering topic '{slug}' not in engineering-topics.yaml"
    if area == "interests":
        if len(parts) < 3:
            return False, "missing interests topic slug"
        slug = parts[2]
        if slug in registries.get("interests-topics", set()):
            return True, f"interests topic '{slug}' in registry"
        return False, f"interests topic '{slug}' not in interests-topics.yaml"
    if area == "clients":
        if len(parts) < 4:
            return False, "missing client slug"
        slug = parts[3]
        if slug in registries.get("clients", set()):
            return True, f"client '{slug}' in registry"
        return False, f"client '{slug}' not in clients.yaml"
    return False, f"unknown wiki area '{area}'"


# ---------------------------------------------------------------------------
# Auto-fix actions
# ---------------------------------------------------------------------------

def find_actual_links(articles: dict[str, str]) -> dict[str, set[str]]:
    """Build a map of article → set of articles it links to."""
    link_map = {}
    for path, content in articles.items():
        links = set()
        # Match [[wikilinks]]
        for match in re.finditer(r"\[\[([^\]|]+)", content):
            target = match.group(1)
            # Normalize: add wiki/ prefix if not present
            if not target.startswith("wiki/"):
                target = f"wiki/{target}"
            if not target.endswith(".md"):
                target += ".md"
            links.add(target)
        link_map[path] = links
    return link_map


def rebuild_backlinks(articles: dict[str, str]) -> str:
    """Rebuild _backlinks.md from actual link state."""
    link_map = find_actual_links(articles)
    now = now_str()

    # Invert: target → set of sources
    inbound = {}
    for source, targets in link_map.items():
        for target in targets:
            inbound.setdefault(target, set()).add(source)

    lines = [
        "---",
        f'title: "Backlink Registry"',
        "type: index",
        f'created: "2026-04-04"',
        f'updated: "{now}"',
        "---",
        "",
        "# Backlink Registry",
        "",
    ]

    for target in sorted(inbound.keys()):
        sources = sorted(inbound[target])
        lines.append(f"## {target}")
        for src in sources:
            lines.append(f"- [[{src}]]")
        lines.append("")

    return "\n".join(lines)


def find_missing_index_entries(articles: dict[str, str], index_md: str) -> list[str]:
    """Find articles not mentioned in _index.md.

    Scoped to the dimensions where _index.md tracking actually adds
    value: concepts/, articles/, and Layer 3 indexes (knowledge/<topic>/index.md
    and industries/<industry>/index.md). Skips:

    - wiki/clients/<status>/<slug>/<file>.md — discoverable via the
      client's own _index.md, not the wiki-wide one.
    - wiki/knowledge/<slug>/<not-index>.md — Layer 2 fragments;
      discoverable via the topic page, not the wiki-wide index.
    - wiki/industries/<slug>/<not-index>.md — same logic, industry page.
    - The well-known meta files.

    Without these exclusions the linter would dump 250+ entries into
    _index.md on the first run after the industries migration.
    """
    skip_files = {
        "wiki/_index.md",
        "wiki/_backlinks.md",
        "wiki/log.md",
        "wiki/home.md",
    }
    missing = []
    for path in articles:
        if path in skip_files:
            continue
        if Path(path).name == "PLACEHOLDER.md":
            continue

        parts = path.split("/")
        # parts[0] == "wiki", parts[1] == area
        if len(parts) >= 2 and parts[1] == "clients":
            # Client folders are self-indexed via wiki/clients/<status>/<slug>/_index.md
            continue

        if len(parts) >= 2 and parts[1] in ("knowledge", "industries", "engineering", "interests"):
            # Only the Layer 3 anchor (index.md) belongs in the global index;
            # skip every Layer 2 fragment across all topic-bearing namespaces.
            if Path(path).name != "index.md":
                continue

        short = path.replace("wiki/", "").replace(".md", "")
        if short not in index_md and path not in index_md:
            title_match = re.search(r'^title:\s*["\']?(.+?)["\']?\s*$',
                                    articles[path], re.MULTILINE)
            title = title_match.group(1) if title_match else Path(path).stem
            missing.append(f"- [[{short}]] — {title}")
    return missing


def _registry_area_map() -> list[tuple[str, str, str]]:
    """Return triples of (wiki_area, registry_key, display_name) for the
    four topic-bearing namespaces that have a matching registry file.
    Clients are handled separately because their directory layout is
    clients/<status>/<slug>/ not clients/<slug>/.
    """
    return [
        ("knowledge",   "topics",             "business topic"),
        ("industries",  "industries",         "industry"),
        ("engineering", "engineering-topics", "engineering topic"),
        ("interests",   "interests-topics",   "interests topic"),
    ]


def detect_registry_drift(registries: dict[str, set[str]]) -> list[dict]:
    """Find directories on disk that exist in wiki/<area>/<slug>/ but
    aren't present in the matching registry YAML.

    These are the same kind of off-registry directories that the April
    rebuild fixed by enforcing registry matching at compile time. If
    any reappear, something upstream is creating them silently (a
    compiler regression, a manual mkdir, a rename that left the old
    folder behind).

    Returns a list of dicts: {area, slug, fragment_count, path}
    """
    drift: list[dict] = []
    for area, registry_key, _ in _registry_area_map():
        area_dir = WIKI_DIR / area
        if not area_dir.exists():
            continue
        canonical = registries.get(registry_key, set())
        for child in sorted(area_dir.iterdir()):
            if not child.is_dir():
                continue
            slug = child.name
            if slug in canonical:
                continue
            frag_count = sum(
                1 for f in child.rglob("*.md")
                if f.name not in ("_index.md", "index.md", "README.md", "PLACEHOLDER.md")
            )
            drift.append({
                "area": area,
                "slug": slug,
                "fragment_count": frag_count,
                "path": f"wiki/{area}/{slug}",
            })
    return drift


def detect_untouched_captures() -> list[dict]:
    """Find fragments in capture/external/ that the classifier never
    touched. These have content but no `classification_confidence`
    field in frontmatter — meaning they were ingested but the
    classifier hasn't run, or ran and crashed before writing back.

    After today's classifier fix this should always be empty: the
    classifier now routes unclassified fragments to
    wiki/engineering/unclassified/ directly, leaving capture empty.
    Worth detecting anyway so we notice if a future ingest fails
    silently.
    """
    untouched: list[dict] = []
    external_dir = CAPTURE_DIR / "external"
    if not external_dir.exists():
        return untouched
    for f in external_dir.rglob("*.md"):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not text.startswith("---"):
            continue
        try:
            end = text.index("\n---\n", 4)
        except ValueError:
            continue
        try:
            fm = yaml.safe_load(text[4:end]) or {}
        except yaml.YAMLError:
            continue
        if not isinstance(fm, dict):
            continue
        if fm.get("classification_confidence"):
            continue  # classifier has seen it
        untouched.append({
            "path": str(f.relative_to(ROOT)),
            "title": fm.get("title", f.stem),
            "source_project": fm.get("source_project", ""),
            "source_date": str(fm.get("source_date", ""))[:10],
        })
    return untouched


def detect_empty_registry_entries(registries: dict[str, set[str]]) -> list[dict]:
    """Find registry entries (slugs in *.yaml) with zero fragments on
    disk. These are candidates for either:
      - Cleanup (slug is genuinely unused, remove from registry)
      - Content backfill (slug is a real topic but has no material yet)

    Topics flagged `synthesize: false` (like engineering `unclassified`)
    are skipped because they're expected to accumulate content over
    time without being synthesized — an empty `unclassified` bucket
    is a healthy state, not a drift warning.

    Returns a list of {area, slug, registry} dicts.
    """
    # Build the set of non-synth slugs per registry so we can skip them
    non_synth = {
        "engineering-topics": _load_non_synthesizable_slugs("engineering-topics.yaml"),
        "interests-topics":   _load_non_synthesizable_slugs("interests-topics.yaml"),
    }

    empty: list[dict] = []
    for area, registry_key, _ in _registry_area_map():
        area_dir = WIKI_DIR / area
        canonical = registries.get(registry_key, set())
        skip = non_synth.get(registry_key, set())
        for slug in sorted(canonical):
            if slug in skip:
                continue
            topic_dir = area_dir / slug
            frag_count = 0
            if topic_dir.exists():
                frag_count = sum(
                    1 for f in topic_dir.rglob("*.md")
                    if f.name not in ("_index.md", "index.md", "README.md", "PLACEHOLDER.md")
                )
            if frag_count == 0:
                empty.append({
                    "area": area,
                    "slug": slug,
                    "registry": f"{registry_key}.yaml",
                })
    return empty


def create_stub(concept: str, slug: str, mentioned_in: list[str],
                location: str) -> str:
    """Create a stub article for a gap concept."""
    now = now_str()
    backlinks = "\n".join(f"- [[{p.replace('wiki/', '').replace('.md', '')}]]"
                          for p in mentioned_in[:10])
    return (
        f"---\n"
        f'title: "{concept}"\n'
        f"type: concept\n"
        f'created: "{now}"\n'
        f'updated: "{now}"\n'
        f"source_docs: []\n"
        f"tags: [stub]\n"
        f"---\n\n"
        f"# {concept}\n\n"
        f"_This is a stub article created by the linter. "
        f"Mentioned in {len(mentioned_in)} articles — "
        f"the compiler will flesh it out on the next run._\n\n"
        f"## Referenced in\n\n{backlinks}\n"
    )


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def generate_report(analysis: dict, actions: list[str], deferred: list[str],
                    article_count: int, dry_run: bool) -> str:
    """Generate the markdown lint report."""
    now = now_str()
    mode = "DRY RUN" if dry_run else "AUTO-FIX"

    lines = [
        f"# Meridian Wiki Health Check",
        f"",
        f"**Generated:** {now}  ",
        f"**Mode:** {mode}  ",
        f"**Articles scanned:** {article_count}  ",
        f"",
    ]

    # Actions taken
    if actions:
        lines.append(f"## Actions Taken ({len(actions)})\n")
        for action in actions:
            lines.append(f"- {action}")
        lines.append("")
    elif not dry_run:
        lines.append("## Actions Taken (0)\n\nNo auto-fixes needed.\n")

    # Deferred items — auto-fixes the linter intentionally skipped for safety
    if deferred:
        lines.append(f"## Held For Review ({len(deferred)})\n")
        lines.append(
            "_The linter declined to apply these automatically. "
            "They are flagged here for human triage._\n"
        )
        for d in deferred:
            lines.append(f"- {d}")
        lines.append("")

    # Contradictions
    contradictions = analysis.get("contradictions", [])
    if contradictions:
        lines.append(f"## Contradictions ({len(contradictions)})\n")
        for c in contradictions:
            lines.append(f"### {c.get('article_a', '?')} vs {c.get('article_b', '?')}")
            lines.append(f"**Claim in A:** {c.get('claim_a', '?')}")
            lines.append(f"**Claim in B:** {c.get('claim_b', '?')}")
            lines.append(f"**Recommended action:** {c.get('recommendation', '?')}")
            lines.append("")

    # Orphans
    orphans = analysis.get("orphans", [])
    if orphans:
        lines.append(f"## Orphans ({len(orphans)})\n")
        for o in orphans:
            lines.append(f"- [[{o.get('path', '?')}]] — {o.get('suggestion', '')}")
        lines.append("")

    # Gaps
    gaps = analysis.get("gaps", [])
    auto_gaps = [g for g in gaps if g.get("mention_count", 0) >= 5]
    flag_gaps = [g for g in gaps if 3 <= g.get("mention_count", 0) < 5]

    if auto_gaps:
        lines.append(f"## Auto-Created Stubs ({len(auto_gaps)})\n")
        for g in auto_gaps:
            lines.append(f"- **{g['concept']}** — mentioned in {g['mention_count']} articles → `{g.get('suggested_location', '?')}`")
        lines.append("")

    if flag_gaps:
        lines.append(f"## Article Candidates ({len(flag_gaps)})\n")
        for g in flag_gaps:
            mentioned = ", ".join(f"[[{p}]]" for p in g.get("mentioned_in", [])[:5])
            lines.append(f"- **{g['concept']}** ({g['mention_count']} mentions) — {mentioned}")
            lines.append(f"  Suggested location: `{g.get('suggested_location', '?')}`")
        lines.append("")

    # Suggested connections
    connections = analysis.get("suggested_connections", [])
    if connections:
        lines.append(f"## Suggested Connections ({len(connections)})\n")
        for c in connections:
            lines.append(f"- [[{c.get('article_a', '?')}]] ↔ [[{c.get('article_b', '?')}]] — {c.get('reason', '')}")
        lines.append("")

    # Client status changes
    status_changes = analysis.get("client_status_changes", [])
    if status_changes:
        lines.append(f"## Client Status Changes ({len(status_changes)})\n")
        for s in status_changes:
            lines.append(
                f"- **{s.get('client', '?')}** — signals suggest "
                f"{s.get('current_status', '?')} → {s.get('suggested_status', '?')}. "
                f"Signal: {s.get('signal', '?')}. Last activity: {s.get('last_activity', '?')}."
            )
        lines.append("")

    # === Deterministic structural checks ===

    # Registry drift — directories on disk not in the registry
    drift = analysis.get("registry_drift", [])
    if drift:
        lines.append(f"## Registry Drift ({len(drift)})\n")
        lines.append(
            "_Directories that exist on disk but aren't in the matching registry. "
            "Either add them to the registry, merge their content into a canonical "
            "topic, or delete them. Left unchecked, these silently inflate filesystem "
            "counts above registered-topic counts._\n"
        )
        for d in drift:
            lines.append(
                f"- `{d['path']}` — {d['fragment_count']} fragment"
                f"{'s' if d['fragment_count'] != 1 else ''} "
                f"(not in {d['area']} registry)"
            )
        lines.append("")

    # Untouched capture fragments — ingested but never classified
    untouched = analysis.get("untouched_captures", [])
    if untouched:
        lines.append(f"## Untouched Capture Fragments ({len(untouched)})\n")
        lines.append(
            "_Fragments in capture/external/ that have content but no "
            "`classification_confidence` field — meaning the classifier "
            "hasn't run against them. Usually means an ingest succeeded "
            "but the follow-up classification pass was skipped or failed._\n"
        )
        for u in untouched[:30]:
            proj = f" [{u['source_project']}]" if u.get("source_project") else ""
            date = f" {u['source_date']}" if u.get("source_date") else ""
            lines.append(f"- `{u['path']}`{proj}{date} — {u['title']}")
        if len(untouched) > 30:
            lines.append(f"- … and {len(untouched) - 30} more")
        lines.append("")

    # Empty registry entries — slugs in YAML with no fragments on disk
    empty_entries = analysis.get("empty_registry_entries", [])
    if empty_entries:
        lines.append(f"## Empty Registry Entries ({len(empty_entries)})\n")
        lines.append(
            "_Slugs registered in a taxonomy YAML that have zero fragments "
            "on disk. Candidates for cleanup (remove from registry) or "
            "backfill (add content). Topics flagged `synthesize: false` "
            "are excluded from this check._\n"
        )
        # Group by area for readability
        by_area: dict[str, list[dict]] = {}
        for e in empty_entries:
            by_area.setdefault(e["area"], []).append(e)
        for area in sorted(by_area):
            entries = by_area[area]
            slugs = ", ".join(f"`{e['slug']}`" for e in entries)
            lines.append(f"- **{area}** ({len(entries)}): {slugs}")
        lines.append("")

    # Summary
    lines.append("## Summary\n")
    lines.append(f"- {len(actions)} auto-fixes applied")
    lines.append(f"- {len(contradictions)} contradictions flagged")
    lines.append(f"- {len(orphans)} orphans flagged")
    lines.append(f"- {len(auto_gaps)} stubs auto-created")
    lines.append(f"- {len(flag_gaps)} new article candidates")
    lines.append(f"- {len(connections)} connections suggested")
    lines.append(f"- {len(status_changes)} client status changes flagged")
    lines.append(f"- {len(drift)} off-registry directories")
    lines.append(f"- {len(untouched)} untouched capture fragments")
    lines.append(f"- {len(empty_entries)} empty registry entries")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Meridian Linter")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report only, no changes")
    parser.add_argument("--scope", default="all",
                        choices=["contradictions", "orphans", "gaps", "all"],
                        help="Which checks to run")
    args = parser.parse_args()

    config = load_config()
    articles = load_all_articles()
    registries = load_all_registries()

    # Filter out index files for article count
    content_articles = {k: v for k, v in articles.items()
                        if Path(k).name not in ("_index.md", "_backlinks.md",
                                                 "log.md", "home.md")}

    if len(content_articles) < 3:
        report = (
            f"# Meridian Wiki Health Check\n\n"
            f"**Generated:** {now_str()}\n\n"
            f"Not enough content to lint ({len(content_articles)} articles). "
            f"Minimum 3 articles needed.\n"
        )
        print(json.dumps({
            "status": "ok",
            "message": "not enough content to lint",
            "article_count": len(content_articles),
        }))
        return

    index_md = load_index()
    backlinks_md = load_backlinks()

    # Run LLM analysis on a proportional sample across dimensions, not the
    # full set. With 3000+ articles the full set would either truncate
    # alphabetically (biased) or blow the context window.
    sampled = load_articles_sampled(char_budget=150_000)
    print(
        f"Analyzing {len(sampled)} sampled wiki articles (of {len(content_articles)} total)...",
        file=sys.stderr,
    )
    client = anthropic.Anthropic()

    try:
        analysis = run_llm_analysis(
            client, index_md, backlinks_md, sampled, registries, config, args.scope
        )
    except Exception as e:
        print(f"LLM analysis failed: {e}", file=sys.stderr)
        analysis = {
            "contradictions": [], "orphans": [], "gaps": [],
            "suggested_connections": [], "client_status_changes": [],
        }

    # Deterministic structural checks that don't need the LLM. These
    # detect issues the compiler + classifier shouldn't be producing
    # anymore — the linter is the safety net that surfaces drift.
    analysis["registry_drift"] = detect_registry_drift(registries)
    analysis["untouched_captures"] = detect_untouched_captures()
    analysis["empty_registry_entries"] = detect_empty_registry_entries(registries)

    # Apply auto-fixes (unless dry-run)
    actions = []
    deferred = []  # human-review items the linter intentionally didn't auto-fix

    if not args.dry_run:
        # 1. Rebuild backlinks (always cheap, uses full article set)
        new_backlinks = rebuild_backlinks(articles)
        if new_backlinks != backlinks_md:
            bl_path = WIKI_DIR / "_backlinks.md"
            bl_path.write_text(new_backlinks, encoding="utf-8")
            actions.append("Rebuilt _backlinks.md to match actual link state")

        # 2. Add missing index entries — capped, dimension-aware.
        missing = find_missing_index_entries(articles, index_md)
        if missing:
            if len(missing) > MAX_INDEX_AUTO_ADDS:
                deferred.append(
                    f"Skipped _index.md auto-update: {len(missing)} entries "
                    f"would have been added (cap is {MAX_INDEX_AUTO_ADDS}). "
                    f"Run a manual review or bump MAX_INDEX_AUTO_ADDS."
                )
            else:
                idx_path = WIKI_DIR / "_index.md"
                content = idx_path.read_text(encoding="utf-8")
                if "## Statistics" in content:
                    content = content.replace(
                        "## Statistics",
                        "\n".join(missing) + "\n\n## Statistics"
                    )
                else:
                    content += "\n" + "\n".join(missing) + "\n"
                idx_path.write_text(content, encoding="utf-8")
                actions.append(f"Added {len(missing)} missing _index.md entries")

        # 3. Create stubs for gaps with 5+ mentions — registry-validated, capped.
        gaps = analysis.get("gaps", [])
        stubs_created = 0
        for gap in gaps:
            if gap.get("mention_count", 0) < 5:
                continue
            if stubs_created >= MAX_AUTO_STUBS:
                deferred.append(
                    f"Stub creation cap reached ({MAX_AUTO_STUBS}); "
                    f"remaining gaps held for review."
                )
                break
            location = gap.get("suggested_location", "")
            if not location:
                deferred.append(
                    f"Gap '{gap.get('concept', '?')}' has no suggested_location — held for human review"
                )
                continue
            allowed, reason = validate_stub_location(location, registries)
            if not allowed:
                deferred.append(
                    f"Rejected stub at {location} — {reason}"
                )
                continue
            stub_path = ROOT / location
            if stub_path.exists():
                continue
            stub_content = create_stub(
                gap["concept"], gap.get("slug", ""),
                gap.get("mentioned_in", []), location
            )
            stub_path.parent.mkdir(parents=True, exist_ok=True)
            stub_path.write_text(stub_content, encoding="utf-8")
            actions.append(
                f"Created stub: {location} — mentioned in "
                f"{gap['mention_count']} articles"
            )
            stubs_created += 1

    # Generate report
    report = generate_report(analysis, actions, deferred, len(content_articles), args.dry_run)

    # Write reports
    now = now_str()
    if not args.dry_run:
        # Full report to outputs/
        OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
        out_path = OUTPUTS_DIR / f"lint-{now}.md"
        out_path.write_text(report, encoding="utf-8")

        # Condensed version to wiki/
        wiki_path = WIKI_DIR / "articles" / f"lint-{now}.md"
        wiki_path.parent.mkdir(parents=True, exist_ok=True)
        wiki_path.write_text(report, encoding="utf-8")

        # Append to log
        log_path = WIKI_DIR / "log.md"
        if log_path.exists():
            log_content = log_path.read_text(encoding="utf-8")
        else:
            log_content = ""
        summary = (
            f"{len(actions)} auto-fixes, "
            f"{len(analysis.get('contradictions', []))} contradictions, "
            f"{len(analysis.get('orphans', []))} orphans, "
            f"{len(analysis.get('gaps', []))} gaps"
        )
        log_content += f"\n## [{now}] lint | Wiki health check — {summary}\n"
        log_path.write_text(log_content, encoding="utf-8")

    output = {
        "status": "ok",
        "article_count": len(content_articles),
        "sampled_count": len(sampled),
        "actions_taken": len(actions),
        "deferred_for_review": len(deferred),
        "contradictions": len(analysis.get("contradictions", [])),
        "orphans": len(analysis.get("orphans", [])),
        "gaps": len(analysis.get("gaps", [])),
        "suggested_connections": len(analysis.get("suggested_connections", [])),
        "report": report,
    }
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
