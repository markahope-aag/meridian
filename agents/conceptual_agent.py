#!/usr/bin/env python3
"""Meridian Conceptual Agent — Phase 7 Layer 4 conceptual layer.

Runs across the existing knowledge base looking for what cannot be
seen from any single Layer 3 article. Four modes:

- Mode A (connections)     — weekly, finds non-obvious cross-topic patterns
- Mode B (maturation)      — weekly, updates confidence on existing patterns
- Mode C (emergence)       — daily, lightweight signal watch
- Mode D (contradictions)  — monthly, resolves cross-topic contradictions

All four share a cached in-memory L3 map built from every Layer 3
index.md in wiki/knowledge/ and wiki/industries/. Cache lives at
cache/layer4/l3_map.json and is invalidated when any source index.md
mtime is newer than the cache (or the schema version drifts).

This is the Phase 7 foundation commit. Mode C is fully implemented;
Modes A, B, and D are stubbed with informative NotImplementedError
messages and will be fleshed out in follow-up commits.

Usage:
    python agents/conceptual_agent.py --mode emergence
    python agents/conceptual_agent.py --mode connections --dry-run
    python agents/conceptual_agent.py --mode maturation
    python agents/conceptual_agent.py --mode contradictions
    python agents/conceptual_agent.py --mode emergence --verbose
    python agents/conceptual_agent.py --status
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, date
from pathlib import Path
from typing import Iterable

import yaml

# Anthropic SDK is only imported inside modes that call it so the
# lightweight Mode B (pure Python) and --status don't require the
# API key env var to be set.

ROOT = Path(__file__).resolve().parent.parent
WIKI_DIR = ROOT / "wiki"
LAYER4_DIR = WIKI_DIR / "layer4"
PATTERNS_DIR = LAYER4_DIR / "patterns"
EMERGENCE_DIR = LAYER4_DIR / "emergence"
CONTRADICTIONS_DIR = LAYER4_DIR / "contradictions"
DRIFT_DIR = LAYER4_DIR / "drift"

KNOWLEDGE_DIR = WIKI_DIR / "knowledge"
INDUSTRIES_DIR = WIKI_DIR / "industries"

CACHE_DIR = ROOT / "cache" / "layer4"
L3_MAP_CACHE = CACHE_DIR / "l3_map.json"
EMERGENCE_CANDIDATES = CACHE_DIR / "emergence_candidates.json"
VERSIONS_ROOT = ROOT / "state" / "synthesis_versions"
SYNTHESIS_QUEUE = ROOT / "synthesis_queue.json"

PROMPTS_DIR = ROOT / "prompts"
OUTPUTS_DIR = ROOT / "outputs"

L3_MAP_SCHEMA_VERSION = 1


# =============================================================================
# Frontmatter parsing and writing (same pattern as evolution_detector.py)
# =============================================================================

def parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    try:
        end = text.index("\n---\n", 4)
    except ValueError:
        return {}, text
    try:
        fm = yaml.safe_load(text[4:end]) or {}
    except yaml.YAMLError:
        return {}, text
    if not isinstance(fm, dict):
        return {}, text
    return fm, text[end + 5 :]


def write_frontmatter(fm: dict, body: str) -> str:
    fm_text = yaml.safe_dump(
        fm, sort_keys=False, default_flow_style=False, allow_unicode=True
    ).strip()
    return f"---\n{fm_text}\n---\n{body}"


def _coerce_date(value) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return datetime.strptime(value[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


def _today() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d")


def _now_stamp() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H-%M-%SZ")


# =============================================================================
# Registry loading (for topics_connected validation)
# =============================================================================

def _load_canonical_slugs(filename: str, keys: Iterable[str]) -> set[str]:
    path = ROOT / filename
    if not path.exists():
        return set()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return set()
    items = []
    for key in keys:
        if isinstance(data.get(key), list):
            items = data[key]
            break
    return {i["slug"] for i in items if isinstance(i, dict) and i.get("slug")}


def load_registries() -> dict[str, set[str]]:
    return {
        "topics":     _load_canonical_slugs("topics.yaml", ["categories", "topics"]),
        "industries": _load_canonical_slugs("industries.yaml", ["industries"]),
    }


# =============================================================================
# L3 map — the shared read layer all four modes consume
# =============================================================================

@dataclass
class L3Summary:
    """Compact representation of one Layer 3 article for cross-topic analysis."""
    dimension: str         # "knowledge" | "industries"
    slug: str
    title: str
    path: str              # repo-relative path (wiki/knowledge/<slug>/index.md)
    confidence: str
    domain_type: str
    evidence_count: int
    first_seen: str
    last_updated: str
    generated_at: str
    # Body-derived content (small samples, cached to disk)
    summary_text: str      # first non-empty paragraph of body (~400 chars)
    key_claims: list[str]  # bullet points extracted from What Works / Patterns sections
    client_mentions: list[str]  # distinct client names cited in body
    related_topics: list[str]   # slugs listed in "## Related Topics" section
    topic_body_tokens: list[str]  # vocabulary sample for cross-topic overlap
    contradicting_sources: list[str]
    mtime: float           # for cache invalidation


@dataclass
class L3Map:
    topics: dict[str, L3Summary]          # slug → summary
    industries: dict[str, L3Summary]      # slug → summary
    topic_client_index: dict[str, set[str]]   # client_name_lower → set of topic slugs
    industry_topic_index: dict[str, set[str]]  # industry slug → set of topics it cites
    topic_industry_index: dict[str, set[str]]  # topic slug → set of industries it cites
    generated_at: str
    schema_version: int


def _extract_summary_text(body: str) -> str:
    """Return the first substantive prose paragraph of a Layer 3 body."""
    # Strip code blocks
    body = re.sub(r"```[^`]*?```", "", body, flags=re.DOTALL)
    # Skip until we hit the first non-heading, non-empty paragraph
    for block in re.split(r"\n\n+", body):
        block = block.strip()
        if not block:
            continue
        if block.startswith("#"):
            continue
        if block.startswith("---"):
            continue
        return block[:400]
    return ""


def _extract_key_claims(body: str, max_claims: int = 15) -> list[str]:
    """Pull bullet claims from the What Works / Patterns sections."""
    claims: list[str] = []
    # Find sections we care about
    for pattern in (
        r"##\s*What\s*Works[^\n]*\n(.*?)(?=\n##|\Z)",
        r"##\s*Patterns\s*Across[^\n]*\n(.*?)(?=\n##|\Z)",
        r"##\s*Current\s*Understanding[^\n]*\n(.*?)(?=\n##|\Z)",
    ):
        m = re.search(pattern, body, re.DOTALL | re.IGNORECASE)
        if not m:
            continue
        section = m.group(1)
        for line in section.split("\n"):
            s = line.strip()
            if s.startswith("- ") or s.startswith("* "):
                # Strip wikilinks for readability
                cleaned = re.sub(r"\[\[[^\]]+\]\]", "", s[2:]).strip()
                if cleaned and len(cleaned) > 20:
                    claims.append(cleaned[:240])
        if len(claims) >= max_claims:
            break
    return claims[:max_claims]


def _extract_client_mentions(body: str, client_name_set: set[str]) -> list[str]:
    """Find known client names appearing in the body."""
    if not client_name_set:
        return []
    body_lower = body.lower()
    mentioned = {name for name in client_name_set if name.lower() in body_lower}
    return sorted(mentioned)


def _extract_related_topics(body: str) -> list[str]:
    """Pull slugs from the Related Topics section."""
    m = re.search(r"##\s*Related\s*Topics[^\n]*\n(.*?)(?=\n##|\Z)", body, re.DOTALL | re.IGNORECASE)
    if not m:
        return []
    section = m.group(1)
    slugs: list[str] = []
    for link in re.findall(r"\[\[([^|\]]+)\]?\]", section):
        # Link like "wiki/knowledge/seo/index.md" or "seo"
        slug = link.strip().split("/")[-1].replace(".md", "")
        if slug in ("index", ""):
            # Back off to the directory name
            parts = [p for p in link.split("/") if p]
            if len(parts) >= 2:
                slug = parts[-2]
        if slug and slug not in ("knowledge", "industries"):
            slugs.append(slug)
    return sorted(set(slugs))


def _tokenize_vocabulary(body: str, max_tokens: int = 200) -> list[str]:
    """Extract distinctive lowercase word tokens from a body for simple
    vocabulary overlap analysis in Mode A / Mode C."""
    stripped = re.sub(r"```[^`]*?```", "", body, flags=re.DOTALL)
    stripped = re.sub(r"\[\[[^\]]+\]\]", "", stripped)
    stripped = re.sub(r"[^a-zA-Z0-9\s\-]", " ", stripped)
    tokens = [t.lower() for t in stripped.split() if len(t) >= 4]
    # Drop common English stopwords to reduce noise in overlap scoring
    stopwords = {
        "that", "this", "with", "from", "they", "them", "their", "have",
        "been", "were", "when", "what", "which", "than", "then", "into",
        "onto", "upon", "also", "some", "more", "most", "such", "each",
        "every", "other", "some", "same", "both", "like", "between",
        "through", "during", "after", "before", "about", "above", "below",
        "where", "while", "will", "would", "could", "should", "being",
        "your", "yours", "theirs", "theres", "here", "there", "over",
        "under", "only", "must", "shall", "make", "made", "takes", "take",
        "going", "goes", "does", "done", "want", "wants", "need", "needs",
        "doing", "means", "meant", "many", "much", "quite", "very",
    }
    tokens = [t for t in tokens if t not in stopwords]
    if max_tokens and len(tokens) > max_tokens:
        # Sample at regular intervals to preserve document breadth
        step = max(1, len(tokens) // max_tokens)
        tokens = tokens[::step][:max_tokens]
    return tokens


def _load_client_name_set() -> set[str]:
    """Return the set of canonical client display names from clients.yaml."""
    path = ROOT / "clients.yaml"
    if not path.exists():
        return set()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return set()
    names: set[str] = set()
    for entry in data.get("clients", []):
        if not isinstance(entry, dict):
            continue
        n = (entry.get("name") or "").strip()
        if n:
            names.add(n)
    return names


def _scan_l3_article(dimension: str, slug: str, idx_path: Path, client_names: set[str]) -> L3Summary | None:
    try:
        text = idx_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    fm, body = parse_frontmatter(text)
    if not fm or fm.get("layer") != 3:
        return None
    rel_path = idx_path.relative_to(ROOT).as_posix()
    summary = L3Summary(
        dimension=dimension,
        slug=slug,
        title=str(fm.get("title") or slug),
        path=rel_path,
        confidence=str(fm.get("confidence") or ""),
        domain_type=str(fm.get("domain_type") or ""),
        evidence_count=int(fm.get("evidence_count") or 0),
        first_seen=str(fm.get("first_seen") or "")[:10],
        last_updated=str(fm.get("last_updated") or "")[:10],
        generated_at=str(fm.get("generated_at") or ""),
        summary_text=_extract_summary_text(body),
        key_claims=_extract_key_claims(body),
        client_mentions=_extract_client_mentions(body, client_names),
        related_topics=_extract_related_topics(body),
        topic_body_tokens=_tokenize_vocabulary(body),
        contradicting_sources=[
            p for p in (fm.get("contradicting_sources") or [])
            if isinstance(p, str)
        ],
        mtime=idx_path.stat().st_mtime,
    )
    return summary


def _build_l3_map() -> L3Map:
    """Walk wiki/knowledge/ and wiki/industries/ and build the full map fresh."""
    client_names = _load_client_name_set()
    topics: dict[str, L3Summary] = {}
    industries: dict[str, L3Summary] = {}

    if KNOWLEDGE_DIR.exists():
        for d in sorted(KNOWLEDGE_DIR.iterdir()):
            if not d.is_dir():
                continue
            idx = d / "index.md"
            if not idx.exists():
                continue
            s = _scan_l3_article("knowledge", d.name, idx, client_names)
            if s is not None:
                topics[d.name] = s

    if INDUSTRIES_DIR.exists():
        for d in sorted(INDUSTRIES_DIR.iterdir()):
            if not d.is_dir():
                continue
            idx = d / "index.md"
            if not idx.exists():
                continue
            s = _scan_l3_article("industries", d.name, idx, client_names)
            if s is not None:
                industries[d.name] = s

    # Cross-reference indexes
    topic_client_index: dict[str, set[str]] = defaultdict(set)
    for slug, summary in topics.items():
        for client in summary.client_mentions:
            topic_client_index[client.lower()].add(slug)

    industry_topic_index: dict[str, set[str]] = defaultdict(set)
    topic_industry_index: dict[str, set[str]] = defaultdict(set)
    # For each topic, note which industries mention it in their Related Topics
    for ind_slug, ind_summary in industries.items():
        for t in ind_summary.related_topics:
            if t in topics:
                industry_topic_index[ind_slug].add(t)
                topic_industry_index[t].add(ind_slug)

    return L3Map(
        topics=topics,
        industries=industries,
        topic_client_index={k: v for k, v in topic_client_index.items()},
        industry_topic_index={k: v for k, v in industry_topic_index.items()},
        topic_industry_index={k: v for k, v in topic_industry_index.items()},
        generated_at=datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        schema_version=L3_MAP_SCHEMA_VERSION,
    )


def _serialize_l3_map(l3map: L3Map) -> dict:
    def _ser_summary(s: L3Summary) -> dict:
        return asdict(s)
    return {
        "schema_version": l3map.schema_version,
        "generated_at": l3map.generated_at,
        "topics": {k: _ser_summary(v) for k, v in l3map.topics.items()},
        "industries": {k: _ser_summary(v) for k, v in l3map.industries.items()},
        "topic_client_index": {k: sorted(list(v)) for k, v in l3map.topic_client_index.items()},
        "industry_topic_index": {k: sorted(list(v)) for k, v in l3map.industry_topic_index.items()},
        "topic_industry_index": {k: sorted(list(v)) for k, v in l3map.topic_industry_index.items()},
    }


def _deserialize_l3_map(data: dict) -> L3Map:
    def _de(d: dict) -> L3Summary:
        return L3Summary(**d)
    return L3Map(
        topics={k: _de(v) for k, v in data.get("topics", {}).items()},
        industries={k: _de(v) for k, v in data.get("industries", {}).items()},
        topic_client_index={k: set(v) for k, v in data.get("topic_client_index", {}).items()},
        industry_topic_index={k: set(v) for k, v in data.get("industry_topic_index", {}).items()},
        topic_industry_index={k: set(v) for k, v in data.get("topic_industry_index", {}).items()},
        generated_at=data.get("generated_at", ""),
        schema_version=data.get("schema_version", 0),
    )


def _newest_index_mtime() -> float:
    """Return the newest mtime across all Layer 3 index.md files we'd scan.
    Used to decide if the cache is stale."""
    newest = 0.0
    for root in (KNOWLEDGE_DIR, INDUSTRIES_DIR):
        if not root.exists():
            continue
        for d in root.iterdir():
            if not d.is_dir():
                continue
            idx = d / "index.md"
            if idx.exists():
                m = idx.stat().st_mtime
                if m > newest:
                    newest = m
    return newest


def load_l3_map(force_refresh: bool = False, verbose: bool = False) -> L3Map:
    """Return the cached L3 map, rebuilding if stale or schema-mismatched."""
    newest_mtime = _newest_index_mtime()

    if not force_refresh and L3_MAP_CACHE.exists():
        try:
            cached = json.loads(L3_MAP_CACHE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            cached = None
        if cached and cached.get("schema_version") == L3_MAP_SCHEMA_VERSION:
            cache_mtime = L3_MAP_CACHE.stat().st_mtime
            if newest_mtime <= cache_mtime:
                if verbose:
                    print("L3 map: cache hit", file=sys.stderr)
                return _deserialize_l3_map(cached)
            else:
                if verbose:
                    print(
                        f"L3 map: stale (newest index.md mtime {newest_mtime:.0f} > cache {cache_mtime:.0f})",
                        file=sys.stderr,
                    )
        elif verbose:
            print("L3 map: schema mismatch or unreadable cache", file=sys.stderr)

    if verbose:
        print("L3 map: rebuilding from disk...", file=sys.stderr)
    l3map = _build_l3_map()

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    L3_MAP_CACHE.write_text(
        json.dumps(_serialize_l3_map(l3map), indent=2, default=str),
        encoding="utf-8",
    )
    if verbose:
        print(
            f"L3 map: cached {len(l3map.topics)} topics + {len(l3map.industries)} industries",
            file=sys.stderr,
        )
    return l3map


# =============================================================================
# Layer 4 directory setup
# =============================================================================

def ensure_layer4_dirs() -> None:
    for d in (LAYER4_DIR, PATTERNS_DIR, EMERGENCE_DIR, CONTRADICTIONS_DIR, DRIFT_DIR):
        d.mkdir(parents=True, exist_ok=True)


# =============================================================================
# Layer 4 article reading (for _index.md generation and Mode B)
# =============================================================================

@dataclass
class Layer4Article:
    path: Path
    concept_type: str      # pattern | emergence | contradiction | drift
    title: str
    topics_connected: list[str]
    industries_connected: list[str]
    confidence: str
    hypothesis: bool
    status: str
    first_detected: str
    last_updated: str
    supporting_count: int
    contradicting_count: int


def _load_layer4_article(path: Path) -> Layer4Article | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    fm, _ = parse_frontmatter(text)
    if not fm or fm.get("layer") != 4:
        return None
    return Layer4Article(
        path=path,
        concept_type=str(fm.get("concept_type") or "pattern"),
        title=str(fm.get("title") or path.stem),
        topics_connected=list(fm.get("topics_connected") or []),
        industries_connected=list(fm.get("industries_connected") or []),
        confidence=str(fm.get("confidence") or "low"),
        hypothesis=bool(fm.get("hypothesis", True)),
        status=str(fm.get("status") or "active"),
        first_detected=str(fm.get("first_detected") or "")[:10],
        last_updated=str(fm.get("last_updated") or "")[:10],
        supporting_count=int(fm.get("supporting_evidence_count") or 0),
        contradicting_count=int(fm.get("contradicting_evidence_count") or 0),
    )


def iter_layer4_articles(concept_type: str | None = None) -> list[Layer4Article]:
    result: list[Layer4Article] = []
    for subdir, ctype in (
        (PATTERNS_DIR, "pattern"),
        (EMERGENCE_DIR, "emergence"),
        (CONTRADICTIONS_DIR, "contradiction"),
        (DRIFT_DIR, "drift"),
    ):
        if concept_type and concept_type != ctype:
            continue
        if not subdir.exists():
            continue
        for f in sorted(subdir.glob("*.md")):
            if f.name == "_index.md":
                continue
            art = _load_layer4_article(f)
            if art is not None:
                result.append(art)
    return result


# =============================================================================
# Layer 4 _index.md regeneration
# =============================================================================

def regenerate_layer4_index() -> Path:
    """Rewrite wiki/layer4/_index.md based on the current article state."""
    ensure_layer4_dirs()
    articles = iter_layer4_articles()

    active_patterns = [
        a for a in articles
        if a.concept_type == "pattern" and not a.hypothesis and a.status == "active"
    ]
    emerging = [
        a for a in articles
        if a.concept_type == "pattern" and a.hypothesis
    ]
    contradictions = [
        a for a in articles if a.concept_type == "contradiction"
    ]
    drift_articles = [
        a for a in articles if a.concept_type == "drift"
    ]

    lines: list[str] = [
        "---",
        'title: "Layer 4 — Conceptual Knowledge"',
        "layer: 4",
        f'last_updated: "{_today()}"',
        "---",
        "",
        "# Layer 4 — Conceptual Knowledge",
        "",
        f"*Last updated: {_today()}*  ",
        "*Maintained by the conceptual agent — do not edit manually.*",
        "",
    ]

    lines.append(f"## Active Patterns ({len(active_patterns)})")
    lines.append("")
    if active_patterns:
        lines.append("| Pattern | Topics Connected | Confidence | Since |")
        lines.append("|---|---|---|---|")
        for a in sorted(active_patterns, key=lambda x: x.first_detected, reverse=True):
            topic_names = ", ".join(
                Path(t).parent.name for t in a.topics_connected[:4]
            ) or "—"
            rel = a.path.relative_to(ROOT).as_posix()
            lines.append(
                f"| [[{rel}|{a.title}]] | {topic_names} | {a.confidence} | {a.first_detected} |"
            )
    else:
        lines.append("_None yet._")
    lines.append("")

    lines.append(f"## Emerging Hypotheses ({len(emerging)})")
    lines.append("*(hypothesis: true — not yet established)*")
    lines.append("")
    if emerging:
        lines.append("| Pattern | Topics Connected | Confidence | Since |")
        lines.append("|---|---|---|---|")
        for a in sorted(emerging, key=lambda x: x.first_detected, reverse=True):
            topic_names = ", ".join(
                Path(t).parent.name for t in a.topics_connected[:4]
            ) or "—"
            rel = a.path.relative_to(ROOT).as_posix()
            lines.append(
                f"| [[{rel}|{a.title}]] | {topic_names} | {a.confidence} | {a.first_detected} |"
            )
    else:
        lines.append("_None yet._")
    lines.append("")

    lines.append(f"## Resolved Contradictions ({len(contradictions)})")
    lines.append("")
    if contradictions:
        lines.append("| Contradiction | Status | Topics |")
        lines.append("|---|---|---|")
        for a in sorted(contradictions, key=lambda x: x.first_detected, reverse=True):
            topic_names = ", ".join(
                Path(t).parent.name for t in a.topics_connected[:4]
            ) or "—"
            rel = a.path.relative_to(ROOT).as_posix()
            lines.append(
                f"| [[{rel}|{a.title}]] | {a.status} | {topic_names} |"
            )
    else:
        lines.append("_None yet._")
    lines.append("")

    lines.append(f"## Knowledge in Drift ({len(drift_articles)})")
    lines.append("*(flagged by Phase 6 evolution detector)*")
    lines.append("")
    if drift_articles:
        for a in sorted(drift_articles, key=lambda x: x.first_detected, reverse=True)[:20]:
            rel = a.path.relative_to(ROOT).as_posix()
            lines.append(f"- [[{rel}|{a.title}]] ({a.first_detected})")
    else:
        lines.append("_None yet._")
    lines.append("")

    content = "\n".join(lines)
    idx_path = LAYER4_DIR / "_index.md"
    idx_path.write_text(content, encoding="utf-8")
    return idx_path


# =============================================================================
# Mode C — Emergence Detection (fully implemented)
# =============================================================================

def _load_emergence_state() -> dict:
    if not EMERGENCE_CANDIDATES.exists():
        return {"last_run": "", "new_evidence_for_patterns": [], "candidate_patterns": []}
    try:
        return json.loads(EMERGENCE_CANDIDATES.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"last_run": "", "new_evidence_for_patterns": [], "candidate_patterns": []}


def _save_emergence_state(state: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    EMERGENCE_CANDIDATES.write_text(
        json.dumps(state, indent=2, default=str),
        encoding="utf-8",
    )


def run_mode_c_emergence(l3map: L3Map, dry_run: bool, verbose: bool) -> dict:
    """Mode C — daily lightweight signal watch.

    Does NOT write Layer 4 articles. Two jobs:

    1. Increment evidence counts on existing Layer 4 patterns by
       checking their topics_connected for new Layer 3 evidence
       that wasn't present at pattern first_detected.
    2. Detect candidate patterns: vocabulary or named entities that
       appear in 2+ otherwise-unconnected Layer 3 articles. Log to
       cache/layer4/emergence_candidates.json. When a candidate
       reaches 3+ appearances across 2+ topics, promote it to the
       synthesis queue as a layer4_candidate so Mode A picks it up.
    """
    state = _load_emergence_state()
    last_run = state.get("last_run", "")

    # ------------------------------------------------------------------
    # Job 1: new evidence for existing patterns
    # ------------------------------------------------------------------
    existing_patterns = iter_layer4_articles(concept_type="pattern")
    new_evidence: list[dict] = []

    for pattern in existing_patterns:
        first_detected = _coerce_date(pattern.first_detected)
        if not first_detected:
            continue
        for topic_path in pattern.topics_connected:
            # Normalize path; expect wiki/knowledge/<slug>/index.md
            parts = topic_path.strip("/").split("/")
            if len(parts) < 4 or parts[0] != "wiki":
                continue
            namespace, slug = parts[1], parts[2]
            if namespace != "knowledge":
                continue
            topic = l3map.topics.get(slug)
            if topic is None:
                continue
            # Look at generated_at — the Layer 3 article was synthesized
            # after the pattern was first detected → fresh evidence
            try:
                topic_gen = datetime.strptime(topic.generated_at[:10], "%Y-%m-%d").date()
            except ValueError:
                continue
            if topic_gen > first_detected:
                new_evidence.append({
                    "pattern": pattern.path.relative_to(ROOT).as_posix(),
                    "source": topic.path,
                    "note": f"topic '{slug}' re-synthesized on {topic_gen} after pattern first_detected {first_detected}",
                })
                if verbose:
                    print(f"  evidence: {pattern.title} ← {slug}", file=sys.stderr)

    # ------------------------------------------------------------------
    # Job 2: candidate pattern detection — vocabulary overlap across
    # unconnected topic articles
    # ------------------------------------------------------------------
    # Build an inverted index of distinctive tokens → topic slugs they appear in
    token_to_topics: dict[str, set[str]] = defaultdict(set)
    for slug, summary in l3map.topics.items():
        # Use the top tokens per topic (most distinctive words)
        counts = Counter(summary.topic_body_tokens)
        # Only consider tokens that appear at least twice within the topic
        for token, n in counts.items():
            if n >= 2 and len(token) >= 6:
                token_to_topics[token].add(slug)

    # Read the set of topic pairs already linked via Related Topics
    linked_pairs: set[tuple[str, str]] = set()
    for slug, summary in l3map.topics.items():
        for related in summary.related_topics:
            if related in l3map.topics:
                a, b = sorted([slug, related])
                linked_pairs.add((a, b))

    # Tokens shared by 2+ topics that aren't already linked are candidate signals
    candidate_tokens: list[dict] = []
    for token, topic_set in token_to_topics.items():
        if len(topic_set) < 2:
            continue
        topic_list = sorted(topic_set)
        # Check if at least one unlinked pair exists in this token's topic set
        has_unlinked_pair = False
        for i, a in enumerate(topic_list):
            for b in topic_list[i + 1:]:
                pair = tuple(sorted([a, b]))
                if pair not in linked_pairs:
                    has_unlinked_pair = True
                    break
            if has_unlinked_pair:
                break
        if not has_unlinked_pair:
            continue
        candidate_tokens.append({
            "signal": token,
            "topics": topic_list,
            "appearances": len(topic_list),
            "first_seen": _today(),
        })

    # Rank candidates by appearance count, keep top 30 to control log size
    candidate_tokens.sort(key=lambda c: -c["appearances"])
    candidate_tokens = candidate_tokens[:30]

    # Merge with previous candidate log — preserve first_seen dates
    previous_candidates = {
        c["signal"]: c for c in state.get("candidate_patterns", [])
        if isinstance(c, dict) and "signal" in c
    }
    merged_candidates: list[dict] = []
    for cand in candidate_tokens:
        prev = previous_candidates.get(cand["signal"])
        if prev:
            cand["first_seen"] = prev.get("first_seen", cand["first_seen"])
        merged_candidates.append(cand)

    # Promote candidates meeting the threshold (3+ appearances, 2+ topics)
    promoted: list[dict] = []
    for cand in merged_candidates:
        if cand["appearances"] >= 3 and len(set(cand["topics"])) >= 2:
            promoted.append(cand)

    # ------------------------------------------------------------------
    # Write state and queue
    # ------------------------------------------------------------------
    state["last_run"] = _today()
    state["new_evidence_for_patterns"] = new_evidence
    state["candidate_patterns"] = merged_candidates

    if not dry_run:
        _save_emergence_state(state)

    # Push promoted candidates into synthesis_queue.json
    queue_added = 0
    if promoted and not dry_run:
        queue: list[dict] = []
        if SYNTHESIS_QUEUE.exists():
            try:
                queue = json.loads(SYNTHESIS_QUEUE.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                queue = []
        if not isinstance(queue, list):
            queue = []
        existing_signals = {
            q.get("signal") for q in queue
            if isinstance(q, dict) and q.get("type") == "layer4_candidate"
        }
        for cand in promoted:
            if cand["signal"] in existing_signals:
                continue
            queue.append({
                "type": "layer4_candidate",
                "signal": cand["signal"],
                "topics": cand["topics"],
                "appearances": cand["appearances"],
                "queued_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "status": "pending",
                "queued_by": "conceptual_agent_mode_c",
            })
            queue_added += 1
        SYNTHESIS_QUEUE.write_text(json.dumps(queue, indent=2), encoding="utf-8")

    return {
        "mode": "emergence",
        "dry_run": dry_run,
        "new_evidence_count": len(new_evidence),
        "candidate_patterns_count": len(merged_candidates),
        "promoted_to_queue": len(promoted) if not dry_run else 0,
        "queue_added": queue_added,
        "last_run": state["last_run"],
    }


# =============================================================================
# Mode A/B/D — stubs for follow-up commits
# =============================================================================

def _linked_pairs(l3map: L3Map) -> set[tuple[str, str]]:
    """Return the set of topic-pair tuples already linked via Related Topics."""
    linked: set[tuple[str, str]] = set()
    for slug, summary in l3map.topics.items():
        for related in summary.related_topics:
            if related in l3map.topics and related != slug:
                a, b = sorted([slug, related])
                linked.add((a, b))
    return linked


def _score_candidate_pair(a: L3Summary, b: L3Summary) -> float:
    """Score a candidate topic pair for potential cross-topic connection.

    Higher score = better candidate. Composed of:
    - vocabulary overlap (common distinctive tokens)
    - shared client mentions (weighted 3x — a client illustrating a
      pattern across topics is the strongest signal in the L3 corpus)
    """
    tokens_a = set(a.topic_body_tokens)
    tokens_b = set(b.topic_body_tokens)
    vocab_overlap = len(tokens_a & tokens_b)
    clients_a = set(c.lower() for c in a.client_mentions)
    clients_b = set(c.lower() for c in b.client_mentions)
    shared_clients = len(clients_a & clients_b)
    return float(vocab_overlap) + 3.0 * float(shared_clients)


def _get_candidate_pairs(l3map: L3Map, max_candidates: int = 12) -> list[tuple[L3Summary, L3Summary, float]]:
    """Return the top-N topic pairs ranked by candidate score, excluding
    pairs already linked via Related Topics."""
    already_linked = _linked_pairs(l3map)
    slugs = sorted(l3map.topics.keys())
    scored: list[tuple[L3Summary, L3Summary, float]] = []
    for i, a_slug in enumerate(slugs):
        a = l3map.topics[a_slug]
        for b_slug in slugs[i + 1:]:
            pair = tuple(sorted([a_slug, b_slug]))
            if pair in already_linked:
                continue
            b = l3map.topics[b_slug]
            score = _score_candidate_pair(a, b)
            if score < 3.0:
                # Floor: no meaningful overlap signal
                continue
            scored.append((a, b, score))
    scored.sort(key=lambda x: -x[2])
    return scored[:max_candidates]


def _read_l3_body(rel_path: str) -> str:
    """Read a Layer 3 article's body (after frontmatter). Used by Mode A
    to give the LLM the full context for each candidate topic."""
    full = ROOT / rel_path
    if not full.exists():
        return ""
    try:
        text = full.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    _, body = parse_frontmatter(text)
    return body.strip()


def _build_candidate_user_message(a: L3Summary, b: L3Summary, a_body: str, b_body: str) -> str:
    """Assemble the user message for a single candidate evaluation."""
    # Truncate bodies to keep the prompt bounded. The LLM only needs
    # enough context to judge whether a genuine connection exists and
    # to cite specific claims — not the full article text.
    max_body_chars = 8000
    if len(a_body) > max_body_chars:
        a_body = a_body[:max_body_chars] + "\n\n[...body truncated]"
    if len(b_body) > max_body_chars:
        b_body = b_body[:max_body_chars] + "\n\n[...body truncated]"

    return (
        f"## Candidate connection\n\n"
        f"You are evaluating whether there is a non-obvious, evidence-grounded\n"
        f"connection between these two Layer 3 articles that would pass the\n"
        f"three-question quality gate in your system prompt.\n\n"
        f"### Topic A: {a.title} (`{a.slug}`)\n"
        f"Path: `{a.path}`  \n"
        f"Confidence: {a.confidence}  \n"
        f"Evidence count: {a.evidence_count}  \n"
        f"Client mentions: {', '.join(a.client_mentions) or '(none)'}  \n\n"
        f"**Body:**\n\n{a_body}\n\n"
        f"---\n\n"
        f"### Topic B: {b.title} (`{b.slug}`)\n"
        f"Path: `{b.path}`  \n"
        f"Confidence: {b.confidence}  \n"
        f"Evidence count: {b.evidence_count}  \n"
        f"Client mentions: {', '.join(b.client_mentions) or '(none)'}  \n\n"
        f"**Body:**\n\n{b_body}\n\n"
        f"---\n\n"
        f"## Your task\n\n"
        f"Apply the three-question quality gate from your system prompt:\n"
        f"1. Does this connection already appear in either article's Related Topics section?\n"
        f"2. Is there at least one piece of non-obvious evidence?\n"
        f"3. Can the connection be stated in one sentence that would surprise a practitioner?\n\n"
        f"If ANY answer is no, reject the candidate. Otherwise, write the Layer 4\n"
        f"article per the template in your system prompt.\n\n"
        f"**Respond with JSON only.** No prose outside the JSON. Schema:\n\n"
        f"```json\n"
        f"{{\n"
        f'  "gate_passed": true | false,\n'
        f'  "reason_if_rejected": "<short explanation if gate_passed is false, otherwise null>",\n'
        f'  "slug": "<kebab-case slug for the new article filename, e.g. \'landing-page-quality-as-forcing-function\'>",\n'
        f'  "article_markdown": "<full markdown content of the Layer 4 pattern article, starting with --- frontmatter, or null if rejected>"\n'
        f"}}\n"
        f"```\n\n"
        f"The article_markdown must be a complete Layer 4 article matching the\n"
        f"template in your system prompt: frontmatter, '## The Connection',\n"
        f"'## Why This Matters', '## Evidence', '## Implication', '## Questions\n"
        f"This Raises'. Use only canonical topic slugs (the two above) in\n"
        f"`topics_connected`. Today's date is {_today()}.\n"
    )


def _evaluate_candidate_with_llm(
    client,  # anthropic.Anthropic
    system_prompt: str,
    model: str,
    a: L3Summary,
    b: L3Summary,
    verbose: bool,
) -> dict | None:
    """Send one candidate to Sonnet, return the parsed JSON response."""
    a_body = _read_l3_body(a.path)
    b_body = _read_l3_body(b.path)
    user_msg = _build_candidate_user_message(a, b, a_body, b_body)

    try:
        response = client.messages.create(
            model=model,
            max_tokens=3000,
            system=system_prompt,
            messages=[{"role": "user", "content": user_msg}],
        )
    except Exception as e:
        if verbose:
            print(f"  LLM error for {a.slug} x {b.slug}: {e}", file=sys.stderr)
        return None

    text = response.content[0].text.strip()
    # Strip optional code fences around the JSON
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    # Extract the outermost JSON object
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        if verbose:
            print(f"  No JSON in response for {a.slug} x {b.slug}", file=sys.stderr)
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError as e:
        if verbose:
            print(f"  JSON parse error for {a.slug} x {b.slug}: {e}", file=sys.stderr)
        return None


def _slugify_connection(value: str, fallback: str = "connection") -> str:
    """Normalize a candidate filename slug."""
    s = re.sub(r"[^a-z0-9\s-]", "", value.lower())
    s = re.sub(r"\s+", "-", s).strip("-")
    s = re.sub(r"-{2,}", "-", s)
    if len(s) > 80:
        s = s[:80].rstrip("-")
    return s or fallback


def _validate_pattern_article(
    article_text: str,
    expected_a: L3Summary,
    expected_b: L3Summary,
    registries: dict[str, set[str]],
) -> tuple[bool, str]:
    """Pre-write validation. Returns (ok, reason_if_not_ok).

    Enforces:
    - Article starts with --- frontmatter
    - layer: 4
    - concept_type: pattern
    - topics_connected contains BOTH expected slugs (as paths)
    - Every topics_connected path resolves to a canonical topic slug
    - Every industries_connected path resolves to a canonical industry slug
    - Article body contains the four required sections
    """
    if not article_text.startswith("---"):
        return False, "article does not start with frontmatter block"

    fm, body = parse_frontmatter(article_text)
    if not fm:
        return False, "frontmatter could not be parsed"

    if fm.get("layer") != 4:
        return False, f"layer is {fm.get('layer')!r}, expected 4"
    if fm.get("concept_type") != "pattern":
        return False, f"concept_type is {fm.get('concept_type')!r}, expected 'pattern'"

    topics_connected = fm.get("topics_connected") or []
    if not isinstance(topics_connected, list) or len(topics_connected) < 2:
        return False, "topics_connected must be a list of at least 2 entries"

    # Extract slugs from paths like wiki/knowledge/<slug>/index.md
    linked_slugs: set[str] = set()
    for entry in topics_connected:
        if not isinstance(entry, str):
            return False, f"topics_connected entry is not a string: {entry!r}"
        parts = entry.strip("/").split("/")
        if len(parts) < 3 or parts[0] != "wiki" or parts[1] not in ("knowledge", "industries"):
            return False, f"topics_connected entry {entry!r} not a valid wiki/knowledge or wiki/industries path"
        slug = parts[2]
        if parts[1] == "knowledge" and slug not in registries.get("topics", set()):
            return False, f"topic slug {slug!r} not in topics.yaml"
        if parts[1] == "industries" and slug not in registries.get("industries", set()):
            return False, f"industry slug {slug!r} not in industries.yaml"
        linked_slugs.add(slug)

    if expected_a.slug not in linked_slugs or expected_b.slug not in linked_slugs:
        return False, (
            f"topics_connected does not include both expected slugs "
            f"({expected_a.slug}, {expected_b.slug})"
        )

    industries_connected = fm.get("industries_connected") or []
    if not isinstance(industries_connected, list):
        return False, "industries_connected must be a list"
    for entry in industries_connected:
        if not isinstance(entry, str):
            continue
        parts = entry.strip("/").split("/")
        if len(parts) >= 3 and parts[0] == "wiki" and parts[1] == "industries":
            slug = parts[2]
            if slug not in registries.get("industries", set()):
                return False, f"industries_connected slug {slug!r} not in industries.yaml"

    # Body structure check — all four required sections
    required_sections = [
        "## The Connection",
        "## Why This Matters",
        "## Evidence",
        "## Implication",
    ]
    for section in required_sections:
        if section not in body:
            return False, f"body missing required section: {section}"

    return True, ""


def _patterns_dir_has_slug(slug: str) -> bool:
    """Return True if a pattern article with that slug already exists."""
    return (PATTERNS_DIR / f"{slug}.md").exists()


def run_mode_a_connections(l3map: L3Map, registries: dict[str, set[str]],
                            dry_run: bool, verbose: bool, limit: int | None = None) -> dict:
    """Mode A — Connection Discovery.

    1. Score candidate topic pairs locally (vocabulary overlap + shared
       clients, excluding pairs already linked via Related Topics).
    2. For each top candidate, read both articles in full and send to
       Sonnet with the connections system prompt. The LLM applies the
       three-question quality gate and either rejects or returns a
       complete Layer 4 article.
    3. Validate the article against the registry + required sections,
       slugify the filename, and write to wiki/layer4/patterns/.
    4. Cap at `limit` articles (default 5).
    """
    import anthropic  # late import — Mode B/C don't need the SDK

    max_articles = limit if limit is not None else 5
    prompt_path = PROMPTS_DIR / "conceptual_connections.md"
    if not prompt_path.exists():
        raise FileNotFoundError(f"missing system prompt: {prompt_path}")
    system_prompt = prompt_path.read_text(encoding="utf-8")

    # Pull the writing-pass model from config.yaml (same as the synthesizer)
    try:
        config = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        config = {}
    model = (
        config.get("compiler", {}).get("writing_model")
        or config.get("llm", {}).get("model")
        or "claude-sonnet-4-6"
    )

    candidates = _get_candidate_pairs(l3map, max_candidates=12)
    if verbose:
        print(f"Mode A: {len(candidates)} candidate pairs above score floor", file=sys.stderr)
        for a, b, score in candidates[:10]:
            print(f"  score={score:5.1f}  {a.slug}  x  {b.slug}", file=sys.stderr)

    client = anthropic.Anthropic()
    written: list[dict] = []
    rejected: list[dict] = []
    validation_failures: list[dict] = []
    llm_errors: list[dict] = []

    for a, b, score in candidates:
        if len(written) >= max_articles:
            if verbose:
                print(f"Mode A: hit max_articles cap ({max_articles}), stopping", file=sys.stderr)
            break

        if verbose:
            print(f"Evaluating {a.slug} x {b.slug} (score {score:.1f})...", file=sys.stderr)

        result = _evaluate_candidate_with_llm(
            client, system_prompt, model, a, b, verbose
        )
        if result is None:
            llm_errors.append({"a": a.slug, "b": b.slug, "score": score})
            continue

        if not result.get("gate_passed"):
            rejection_reason = result.get("reason_if_rejected") or "no reason given"
            rejected.append({
                "a": a.slug,
                "b": b.slug,
                "score": score,
                "reason": rejection_reason,
            })
            if verbose:
                print(f"  rejected: {rejection_reason}", file=sys.stderr)
            continue

        article_markdown = result.get("article_markdown") or ""
        slug_suggestion = result.get("slug") or f"{a.slug}-{b.slug}"
        slug = _slugify_connection(slug_suggestion, fallback=f"{a.slug}-{b.slug}")

        # Validate the article
        ok, reason = _validate_pattern_article(article_markdown, a, b, registries)
        if not ok:
            validation_failures.append({
                "a": a.slug,
                "b": b.slug,
                "slug": slug,
                "reason": reason,
            })
            if verbose:
                print(f"  validation failed: {reason}", file=sys.stderr)
            continue

        # Avoid collisions with existing files
        base_slug = slug
        counter = 2
        while _patterns_dir_has_slug(slug):
            slug = f"{base_slug}-{counter}"
            counter += 1
            if counter > 10:
                break

        target_path = PATTERNS_DIR / f"{slug}.md"

        written.append({
            "a": a.slug,
            "b": b.slug,
            "score": score,
            "slug": slug,
            "path": str(target_path.relative_to(ROOT)),
        })

        if not dry_run:
            ensure_layer4_dirs()
            target_path.write_text(article_markdown, encoding="utf-8")
            if verbose:
                print(f"  wrote {target_path.relative_to(ROOT)}", file=sys.stderr)

    return {
        "mode": "connections",
        "dry_run": dry_run,
        "candidates_evaluated": len(written) + len(rejected) + len(validation_failures) + len(llm_errors),
        "written": written,
        "rejected": rejected,
        "validation_failures": validation_failures,
        "llm_errors": llm_errors,
        "model": model,
    }


def _confidence_from_count(supporting: int) -> str:
    """Map evidence count to confidence level per the Layer 4 gradation."""
    if supporting >= 10:
        return "established"
    if supporting >= 5:
        return "high"
    if supporting >= 3:
        return "medium"
    return "low"


def _archive_layer4(path: Path) -> Path:
    """Copy a Layer 4 article to state/synthesis_versions/layer4/<subdir>/<slug>/<timestamp>.md
    before modifying it. Mirrors the synthesizer's archive_existing_synthesis."""
    subdir = path.parent.name  # patterns | emergence | contradictions | drift
    slug = path.stem
    versions = VERSIONS_ROOT / "layer4" / subdir / slug
    versions.mkdir(parents=True, exist_ok=True)
    dst = versions / f"{_now_stamp()}.md"
    import shutil
    shutil.copy2(path, dst)
    return dst


def _count_pattern_evidence(
    pattern: Layer4Article,
    l3map: L3Map,
    contradiction_keywords: list[str],
) -> tuple[int, int]:
    """Count supporting and contradicting evidence for a pattern article.

    Supporting evidence: a Layer 3 article in topics_connected that was
    (re)generated after the pattern's first_detected — meaning fresh
    information has come in that doesn't contradict the pattern.

    Contradicting evidence: a Layer 3 article in topics_connected whose
    body contains strong contradiction-keyword language in its prose
    sections (code blocks stripped, same logic as the evolution detector).
    """
    first_detected = _coerce_date(pattern.first_detected)
    supporting = 0
    contradicting = 0

    for topic_path in pattern.topics_connected:
        parts = topic_path.strip("/").split("/")
        if len(parts) < 4 or parts[0] != "wiki":
            continue
        namespace, slug = parts[1], parts[2]
        if namespace not in ("knowledge", "industries"):
            continue
        summary = (l3map.topics if namespace == "knowledge" else l3map.industries).get(slug)
        if summary is None:
            continue

        # Read full body for contradiction scanning
        full_body = _read_l3_body(summary.path)
        if not full_body:
            continue
        prose = re.sub(r"```[^`]*?```", "", full_body, flags=re.DOTALL)
        prose_lower = prose.lower()

        is_contradicting = any(kw in prose_lower for kw in contradiction_keywords)

        if is_contradicting:
            contradicting += 1
            continue

        # Supporting: was it generated after the pattern was first detected?
        try:
            topic_gen = datetime.strptime(summary.generated_at[:10], "%Y-%m-%d").date()
        except ValueError:
            topic_gen = None
        if first_detected and topic_gen and topic_gen >= first_detected:
            supporting += 1
        elif first_detected is None:
            # Pattern has no first_detected date — treat every linked article as supporting
            supporting += 1

    return supporting, contradicting


def run_mode_b_maturation(l3map: L3Map, dry_run: bool, verbose: bool) -> dict:
    """Mode B — Pattern Maturation.

    Walks every wiki/layer4/patterns/*.md article, counts current
    supporting and contradicting evidence across its connected topics,
    and updates the article's frontmatter:
      - supporting_evidence_count: recomputed
      - contradicting_evidence_count: recomputed
      - confidence: from the supporting count (low/medium/high/established)
      - hypothesis: flipped to false when confidence >= medium AND
        contradicting_evidence_count == 0
      - last_updated: today
      - status: set to "active" if contradictions exist but unresolved,
        otherwise left as-is

    Pure Python — no LLM calls. Uses synthesis versioning before every
    mutation so every change is recoverable.
    """
    # Load contradiction keywords from config.yaml (reuse the evolution
    # detector's tuned list — same signals matter for pattern maturation).
    try:
        config = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        config = {}
    contradiction_keywords = [
        k.lower() for k in
        config.get("evolution", {}).get("contradiction_keywords", [
            "no longer", "deprecated", "superseded by", "replaced by",
            "now requires", "breaking change", "no longer supported",
            "removed in",
        ])
    ]

    patterns = iter_layer4_articles(concept_type="pattern")
    if verbose:
        print(f"Mode B: reviewing {len(patterns)} pattern articles", file=sys.stderr)

    updates: list[dict] = []
    unchanged = 0

    for pattern in patterns:
        supporting, contradicting = _count_pattern_evidence(
            pattern, l3map, contradiction_keywords
        )
        new_confidence = _confidence_from_count(supporting)
        new_hypothesis = pattern.hypothesis
        if new_confidence in ("medium", "high", "established") and contradicting == 0:
            new_hypothesis = False

        changes: dict = {}
        if supporting != pattern.supporting_count:
            changes["supporting_evidence_count"] = {
                "before": pattern.supporting_count,
                "after": supporting,
            }
        if contradicting != pattern.contradicting_count:
            changes["contradicting_evidence_count"] = {
                "before": pattern.contradicting_count,
                "after": contradicting,
            }
        if new_confidence != pattern.confidence:
            changes["confidence"] = {
                "before": pattern.confidence,
                "after": new_confidence,
            }
        if new_hypothesis != pattern.hypothesis:
            changes["hypothesis"] = {
                "before": pattern.hypothesis,
                "after": new_hypothesis,
            }

        if not changes:
            unchanged += 1
            continue

        if verbose:
            print(f"  {pattern.title}: {len(changes)} change(s)", file=sys.stderr)

        update_record = {
            "path": str(pattern.path.relative_to(ROOT)),
            "title": pattern.title,
            "changes": changes,
        }

        if not dry_run:
            # Re-read the article to update frontmatter in place
            try:
                text = pattern.path.read_text(encoding="utf-8", errors="replace")
            except OSError as e:
                update_record["error"] = f"read failed: {e}"
                updates.append(update_record)
                continue
            fm, body = parse_frontmatter(text)
            if not fm:
                update_record["error"] = "frontmatter parse failed"
                updates.append(update_record)
                continue

            # Archive before writing
            archived = _archive_layer4(pattern.path)
            update_record["archived_to"] = str(archived.relative_to(ROOT))

            fm["supporting_evidence_count"] = supporting
            fm["contradicting_evidence_count"] = contradicting
            fm["confidence"] = new_confidence
            fm["hypothesis"] = new_hypothesis
            fm["last_updated"] = _today()

            new_text = write_frontmatter(fm, body)
            pattern.path.write_text(new_text, encoding="utf-8")

        updates.append(update_record)

    return {
        "mode": "maturation",
        "dry_run": dry_run,
        "patterns_reviewed": len(patterns),
        "updates_applied": len(updates),
        "unchanged": unchanged,
        "updates": updates,
    }


def _find_l3_articles_with_contradictions(l3map: L3Map) -> list[L3Summary]:
    """Return L3 summaries that have non-empty contradicting_sources."""
    result: list[L3Summary] = []
    for summary in list(l3map.topics.values()) + list(l3map.industries.values()):
        if summary.contradicting_sources:
            result.append(summary)
    return result


def _read_contradicting_source(path_str: str) -> tuple[str, str]:
    """Read a contradicting-source fragment's title + body. The paths
    in contradicting_sources are repo-relative strings like
    'wiki/knowledge/seo/bluepoint-state-pages.md'."""
    full = ROOT / path_str
    if not full.exists():
        return ("", "")
    try:
        text = full.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ("", "")
    fm, body = parse_frontmatter(text)
    title = str(fm.get("title", full.stem)) if fm else full.stem
    # Truncate to bound the prompt
    body = body.strip()[:6000]
    return (title, body)


def _build_contradiction_user_message(
    article: L3Summary,
    supporting_claims: str,
    supporting_paths: list[str],
    contradicting_sources: list[tuple[str, str, str]],  # (path, title, body)
) -> str:
    """Assemble the user message for one contradiction-resolution call."""
    parts: list[str] = []
    parts.append(f"## The Layer 3 article with the contradiction\n")
    parts.append(f"**Topic:** {article.title} (`{article.slug}`)  ")
    parts.append(f"**Path:** `{article.path}`  ")
    parts.append(f"**Domain type:** {article.domain_type}  ")
    parts.append(f"**Confidence:** {article.confidence}  ")
    parts.append("")
    parts.append("### Supporting claims (from the Layer 3 article's own body)\n")
    parts.append(supporting_claims[:4000] or "_(no explicit supporting claims extracted — see full article on disk)_")
    parts.append("")

    parts.append("## Contradicting sources\n")
    if not contradicting_sources:
        parts.append("_(no contradicting sources readable from disk)_")
    for i, (path, title, body) in enumerate(contradicting_sources, 1):
        parts.append(f"### Contradicting source #{i}: {title}")
        parts.append(f"**Path:** `{path}`")
        parts.append("")
        parts.append(body[:3000] or "_(empty body)_")
        parts.append("")

    parts.append("---\n")
    parts.append("## Your task\n")
    parts.append(
        "Apply the five-frame explanation framework from your system prompt\n"
        "(industry / size / timeline / methodology / context) to explain the\n"
        "apparent contradiction between the Layer 3 article's supporting\n"
        "claims and the contradicting sources above.\n\n"
        "If you can produce a grounded one-sentence decision rule, write a\n"
        "Layer 4 contradiction-resolution article per the template in your\n"
        "system prompt and return status=resolved. If you cannot resolve\n"
        "the contradiction from the evidence above, return status=unresolved\n"
        "and flag for web augmentation.\n\n"
    )
    parts.append("**Respond with JSON only.** Schema:\n\n")
    parts.append(
        '```json\n'
        '{\n'
        '  "slug": "<kebab-case filename slug>",\n'
        '  "status": "resolved" | "unresolved",\n'
        '  "frame": "industry" | "size" | "timeline" | "methodology" | "context" | null,\n'
        '  "decision_rule": "<one-sentence rule if resolved, otherwise null>",\n'
        '  "article_markdown": "<full markdown content, starting with --- frontmatter>",\n'
        '  "annotate_source": true | false,\n'
        '  "source_annotation": "<short markdown block to append to the source article\'s Evolution and Change section if annotate_source is true>"\n'
        '}\n'
        '```\n\n'
    )
    parts.append(
        f"The article_markdown must be a complete Layer 4 contradiction article\n"
        f"matching the template in your system prompt. Use only the canonical\n"
        f"topic/industry slugs from the paths above. Today's date is {_today()}.\n"
    )
    return "\n".join(parts)


def _validate_contradiction_article(
    article_text: str,
    expected_topic: L3Summary,
    registries: dict[str, set[str]],
) -> tuple[bool, str]:
    """Validate a Layer 4 contradiction-resolution article before writing."""
    if not article_text.startswith("---"):
        return False, "article does not start with frontmatter block"

    fm, body = parse_frontmatter(article_text)
    if not fm:
        return False, "frontmatter could not be parsed"

    if fm.get("layer") != 4:
        return False, f"layer is {fm.get('layer')!r}, expected 4"
    if fm.get("concept_type") != "contradiction":
        return False, f"concept_type is {fm.get('concept_type')!r}, expected 'contradiction'"
    if fm.get("status") not in ("resolved", "unresolved"):
        return False, f"status {fm.get('status')!r} must be 'resolved' or 'unresolved'"

    topics_connected = fm.get("topics_connected") or []
    if not isinstance(topics_connected, list) or not topics_connected:
        return False, "topics_connected must be a non-empty list"

    found_expected = False
    for entry in topics_connected:
        if not isinstance(entry, str):
            return False, f"topics_connected entry is not a string: {entry!r}"
        parts = entry.strip("/").split("/")
        if len(parts) < 3 or parts[0] != "wiki" or parts[1] not in ("knowledge", "industries"):
            return False, f"topics_connected entry {entry!r} not a valid wiki/knowledge or wiki/industries path"
        slug = parts[2]
        if parts[1] == "knowledge" and slug not in registries.get("topics", set()):
            return False, f"topic slug {slug!r} not in topics.yaml"
        if parts[1] == "industries" and slug not in registries.get("industries", set()):
            return False, f"industry slug {slug!r} not in industries.yaml"
        if slug == expected_topic.slug:
            found_expected = True

    if not found_expected:
        return False, (
            f"topics_connected does not include the source topic slug "
            f"{expected_topic.slug!r}"
        )

    # Body structure check — contradiction articles have different required
    # sections than pattern articles
    required_sections = ["## The Contradiction", "## The Resolution"]
    for section in required_sections:
        if section not in body:
            return False, f"body missing required section: {section}"

    return True, ""


def _annotate_source_article(
    source_path: Path,
    annotation_markdown: str,
    resolution_slug: str,
    dry_run: bool,
) -> tuple[bool, str]:
    """Append a 'Contradiction resolved' note to the ## Evolution and Change
    section of a Layer 3 article. Archives before modification so every
    edit is recoverable."""
    if not source_path.exists():
        return False, f"source article does not exist: {source_path}"
    try:
        text = source_path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return False, f"read failed: {e}"
    fm, body = parse_frontmatter(text)
    if not fm:
        return False, "source article has no parseable frontmatter"

    # Find or create the "## Evolution and Change" section
    marker = "## Evolution and Change"
    annotation_block = (
        f"\n\n### Contradiction resolved: {resolution_slug}\n\n"
        f"{annotation_markdown.strip()}\n"
    )

    if marker in body:
        # Append to the end of the Evolution and Change section
        # (before the next top-level section or end of file)
        pattern = rf"({re.escape(marker)}.*?)(?=\n##\s|\Z)"
        match = re.search(pattern, body, re.DOTALL)
        if match:
            before = body[:match.end()]
            after = body[match.end():]
            new_body = before.rstrip() + annotation_block + after
        else:
            new_body = body + annotation_block
    else:
        # Section doesn't exist; add it at the end
        new_body = body.rstrip() + f"\n\n{marker}\n{annotation_block}"

    if dry_run:
        return True, "would annotate (dry-run, no write)"

    # Archive the source L3 article before modifying
    subdir = "topic" if "/knowledge/" in str(source_path) else "industry"
    slug = source_path.parent.name
    versions = VERSIONS_ROOT / subdir / slug
    versions.mkdir(parents=True, exist_ok=True)
    import shutil
    archive = versions / f"{_now_stamp()}.md"
    shutil.copy2(source_path, archive)

    new_text = write_frontmatter(fm, new_body)
    source_path.write_text(new_text, encoding="utf-8")
    return True, f"annotated, archived to {archive.relative_to(ROOT)}"


def run_mode_d_contradictions(l3map: L3Map, registries: dict[str, set[str]],
                                dry_run: bool, verbose: bool) -> dict:
    """Mode D — Contradiction Resolution.

    For each Layer 3 article with non-empty contradicting_sources:

    1. Read the full article body from disk for supporting context.
    2. Read each contradicting source fragment from disk.
    3. Send both to Sonnet with the contradictions system prompt.
    4. Expect JSON back: either a resolved resolution article with a
       one-sentence decision rule, or an unresolved flag for web
       augmentation.
    5. Validate the article (frontmatter, registry, required sections).
    6. Write to wiki/layer4/contradictions/<slug>.md.
    7. If `annotate_source` is true and resolved, append a
       "Contradiction resolved" note to the source Layer 3 article's
       Evolution and Change section — archived via synthesis versioning
       so every edit is recoverable.
    """
    import anthropic

    prompt_path = PROMPTS_DIR / "conceptual_contradictions.md"
    if not prompt_path.exists():
        raise FileNotFoundError(f"missing system prompt: {prompt_path}")
    system_prompt = prompt_path.read_text(encoding="utf-8")

    try:
        config = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        config = {}
    model = (
        config.get("compiler", {}).get("writing_model")
        or config.get("llm", {}).get("model")
        or "claude-sonnet-4-6"
    )

    candidates = _find_l3_articles_with_contradictions(l3map)
    if verbose:
        print(f"Mode D: {len(candidates)} Layer 3 articles have contradicting_sources", file=sys.stderr)

    if not candidates:
        return {
            "mode": "contradictions",
            "dry_run": dry_run,
            "candidates": 0,
            "resolved": [],
            "unresolved": [],
            "validation_failures": [],
            "llm_errors": [],
        }

    client = anthropic.Anthropic()
    resolved: list[dict] = []
    unresolved: list[dict] = []
    validation_failures: list[dict] = []
    llm_errors: list[dict] = []

    for article in candidates:
        if verbose:
            print(f"Resolving contradictions for {article.slug}...", file=sys.stderr)

        # Read supporting claims from the article's body
        _, supporting_body = parse_frontmatter(
            (ROOT / article.path).read_text(encoding="utf-8", errors="replace")
            if (ROOT / article.path).exists() else ""
        )
        # Read each contradicting source
        sources_read: list[tuple[str, str, str]] = []
        for src_path in article.contradicting_sources[:5]:  # cap at 5
            title, body = _read_contradicting_source(src_path)
            if title:
                sources_read.append((src_path, title, body))

        if not sources_read:
            unresolved.append({
                "topic": article.slug,
                "reason": "no contradicting sources readable from disk",
            })
            continue

        user_msg = _build_contradiction_user_message(
            article, supporting_body, article.contradicting_sources, sources_read
        )

        try:
            response = client.messages.create(
                model=model,
                max_tokens=3000,
                system=system_prompt,
                messages=[{"role": "user", "content": user_msg}],
            )
        except Exception as e:
            llm_errors.append({"topic": article.slug, "error": str(e)})
            if verbose:
                print(f"  LLM error: {e}", file=sys.stderr)
            continue

        text = response.content[0].text.strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            llm_errors.append({"topic": article.slug, "error": "no JSON in response"})
            continue
        try:
            result = json.loads(match.group(0))
        except json.JSONDecodeError as e:
            llm_errors.append({"topic": article.slug, "error": f"JSON parse: {e}"})
            continue

        slug_suggestion = result.get("slug") or f"{article.slug}-contradiction"
        slug = _slugify_connection(slug_suggestion, fallback=f"{article.slug}-contradiction")
        article_markdown = result.get("article_markdown") or ""
        status = result.get("status", "unresolved")

        if not article_markdown:
            validation_failures.append({
                "topic": article.slug,
                "slug": slug,
                "reason": "LLM returned no article_markdown",
            })
            continue

        ok, reason = _validate_contradiction_article(article_markdown, article, registries)
        if not ok:
            validation_failures.append({
                "topic": article.slug,
                "slug": slug,
                "reason": reason,
            })
            if verbose:
                print(f"  validation failed: {reason}", file=sys.stderr)
            continue

        # Avoid collisions
        base_slug = slug
        counter = 2
        while (CONTRADICTIONS_DIR / f"{slug}.md").exists():
            slug = f"{base_slug}-{counter}"
            counter += 1
            if counter > 10:
                break

        target_path = CONTRADICTIONS_DIR / f"{slug}.md"

        record = {
            "topic": article.slug,
            "slug": slug,
            "path": str(target_path.relative_to(ROOT)),
            "status": status,
            "frame": result.get("frame"),
            "decision_rule": result.get("decision_rule"),
        }

        if not dry_run:
            ensure_layer4_dirs()
            target_path.write_text(article_markdown, encoding="utf-8")

            # Annotate the source Layer 3 article (if the LLM requested it)
            if status == "resolved" and result.get("annotate_source") and result.get("source_annotation"):
                source_path = ROOT / article.path
                annotated, note = _annotate_source_article(
                    source_path,
                    result["source_annotation"],
                    slug,
                    dry_run=False,
                )
                record["source_annotation"] = note
                record["source_annotation_ok"] = annotated

        if status == "resolved":
            resolved.append(record)
        else:
            unresolved.append(record)

    return {
        "mode": "contradictions",
        "dry_run": dry_run,
        "candidates": len(candidates),
        "resolved": resolved,
        "unresolved": unresolved,
        "validation_failures": validation_failures,
        "llm_errors": llm_errors,
        "model": model,
    }


# =============================================================================
# Status command
# =============================================================================

def run_status() -> dict:
    """Read the current Layer 4 state and return a compact summary."""
    articles = iter_layer4_articles()
    by_type: dict[str, int] = defaultdict(int)
    by_confidence: dict[str, int] = defaultdict(int)
    hypothesis_count = 0
    for a in articles:
        by_type[a.concept_type] += 1
        by_confidence[a.confidence] += 1
        if a.hypothesis:
            hypothesis_count += 1
    return {
        "total_layer4_articles": len(articles),
        "by_type": dict(by_type),
        "by_confidence": dict(by_confidence),
        "hypothesis_count": hypothesis_count,
        "drift_count": by_type.get("drift", 0),
        "emergence_state_path": str(EMERGENCE_CANDIDATES.relative_to(ROOT)),
        "l3_map_cache_path": str(L3_MAP_CACHE.relative_to(ROOT)),
    }


# =============================================================================
# Main
# =============================================================================

def main() -> int:
    parser = argparse.ArgumentParser(description="Meridian Conceptual Agent — Layer 4")
    parser.add_argument(
        "--mode",
        choices=["connections", "maturation", "emergence", "contradictions"],
        help="Which mode to run",
    )
    parser.add_argument("--dry-run", action="store_true", help="Report only, no writes")
    parser.add_argument("--verbose", action="store_true", help="Verbose logging")
    parser.add_argument("--status", action="store_true", help="Print Layer 4 status and exit")
    parser.add_argument(
        "--limit", type=int, default=None,
        help="For Mode A: cap the number of articles written (for first-run safety)",
    )
    args = parser.parse_args()

    ensure_layer4_dirs()

    if args.status:
        status = run_status()
        print(json.dumps(status, indent=2))
        return 0

    if not args.mode:
        parser.error("one of --mode or --status is required")

    if args.verbose:
        print(f"Conceptual agent: mode={args.mode} dry_run={args.dry_run}", file=sys.stderr)

    l3map = load_l3_map(verbose=args.verbose)
    registries = load_registries()

    if args.verbose:
        print(
            f"L3 map: {len(l3map.topics)} topics, {len(l3map.industries)} industries",
            file=sys.stderr,
        )

    try:
        if args.mode == "connections":
            result = run_mode_a_connections(
                l3map, registries, args.dry_run, args.verbose, limit=args.limit
            )
        elif args.mode == "maturation":
            result = run_mode_b_maturation(l3map, args.dry_run, args.verbose)
        elif args.mode == "emergence":
            result = run_mode_c_emergence(l3map, args.dry_run, args.verbose)
        elif args.mode == "contradictions":
            result = run_mode_d_contradictions(
                l3map, registries, args.dry_run, args.verbose
            )
        else:
            parser.error(f"unknown mode: {args.mode}")
    except NotImplementedError as e:
        print(json.dumps({
            "status": "not_implemented",
            "mode": args.mode,
            "message": str(e),
        }, indent=2))
        return 2

    # Regenerate the Layer 4 index after any mode that wrote anything
    if not args.dry_run:
        regenerate_layer4_index()

    print(json.dumps({"status": "ok", **result}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
