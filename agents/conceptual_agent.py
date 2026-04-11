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

def run_mode_a_connections(l3map: L3Map, registries: dict[str, set[str]],
                            dry_run: bool, verbose: bool, limit: int | None = None) -> dict:
    """Mode A — Connection Discovery (NOT YET IMPLEMENTED in this commit).

    Reads L3 map, finds non-obvious cross-topic connections via Sonnet,
    writes at most 5 pattern articles to wiki/layer4/patterns/. Uses
    prompts/conceptual_connections.md. Hard quality gate before writing:
    not already linked, at least 2 pieces of evidence, surprising to a
    practitioner. This will be built in the next Phase 7 commit.
    """
    raise NotImplementedError(
        "Mode A (connections) not implemented yet — follow-up commit. "
        "Foundation, prompts, and shared L3 map are in place; next commit "
        "wires the Sonnet writing loop + quality gate."
    )


def run_mode_b_maturation(l3map: L3Map, dry_run: bool, verbose: bool) -> dict:
    """Mode B — Pattern Maturation (NOT YET IMPLEMENTED in this commit).

    Reads existing wiki/layer4/patterns/*.md, counts new supporting +
    contradicting evidence since first_detected, updates confidence
    per the standard evidence gradation, flips hypothesis: false when
    confidence reaches medium+ with zero contradictions. Pure Python —
    no LLM calls. Uses synthesis versioning before any mutation.
    Will be built in the next Phase 7 commit.
    """
    raise NotImplementedError(
        "Mode B (maturation) not implemented yet — follow-up commit. "
        "Foundation (archiving, frontmatter rewriting) is in place; "
        "next commit adds the evidence-counting + confidence update loop."
    )


def run_mode_d_contradictions(l3map: L3Map, registries: dict[str, set[str]],
                                dry_run: bool, verbose: bool) -> dict:
    """Mode D — Contradiction Resolution (NOT YET IMPLEMENTED in this commit).

    Walks Layer 3 articles with non-empty contradicting_sources,
    attempts to explain each via the 5-frame framework (industry /
    size / timeline / methodology / context), writes resolution
    articles to wiki/layer4/contradictions/. Uses Sonnet via
    prompts/conceptual_contradictions.md. Adds "Contradiction Resolved"
    notes to source Layer 3 articles via synthesis versioning.
    Will be built in the next Phase 7 commit.
    """
    raise NotImplementedError(
        "Mode D (contradictions) not implemented yet — follow-up commit. "
        "Foundation, prompts, and shared L3 map are in place; next commit "
        "wires the Sonnet resolution loop + source article annotation."
    )


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
