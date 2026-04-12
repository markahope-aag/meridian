#!/usr/bin/env python3
"""Meridian Evolution Detector — Phase 6 temporal intelligence.

Scans Layer 3 index.md files across all knowledge namespaces for signs
that their underlying knowledge is changing or has changed. Updates
evolution tracking fields (evolution_timeline, evolution_start,
current_status) on articles where evolution is detected, and writes
a weekly report to outputs/evolution-<date>.md.

Runs weekly on Sundays at 08:00 UTC after the Saturday linter.

## The four detection checks

1. **Contradiction accumulation** — new Layer 2 fragments (added since
   the article's last_updated) contain keywords signaling disagreement
   with established patterns. Per-domain sensitivity thresholds.

2. **Source date divergence** — supporting_sources are old but
   contradicting_sources are recent (>6 months newer on average).
   Classic pattern-evolution signature.

3. **Platform/regulatory trigger** — new fragment explicitly describes
   a platform policy change or regulatory update. Immediate high-priority
   flag regardless of domain sensitivity.

4. **Confidence decay** — article is old (>6 months) with no new
   evidence, and lives in a fast-moving domain. Knowledge may be
   silently going stale.

## What it modifies

On detection, the detector:
- Archives the current index.md via the synthesizer's versioning
  mechanism (state/synthesis_versions/<dim>/<slug>/<timestamp>.md)
- Updates frontmatter fields in place:
    - evolution_timeline: appends a new entry {date, event, note, check}
    - evolution_start: set to detection date (if not already set)
    - current_status: set to "evolving" for checks 1-3 (check 4 leaves
      status alone, only adds a timeline note)
    - contradicting_count: recomputed from contradicting_sources
- For check 3 (platform/regulatory), additionally creates a
  wiki/layer4/drift/<dim>-<slug>-<date>.md flag for human review
- Optionally appends to synthesis_queue.json with priority: high if
  check 4 triggers (confidence decay → suggests re-synthesis)

## What it does NOT do

- Does not rewrite article bodies. The body of a Layer 3 article is
  only rewritten by the synthesizer, not the detector.
- Does not retroactively backfill the new evolution fields on
  articles that pre-date Phase 6. It adds them when it touches an
  article, but otherwise leaves old frontmatter alone.
- Does not change confidence or domain_type. Those are editorial
  decisions owned by the synthesizer.

Usage:
    python agents/evolution_detector.py              # full run, updates articles
    python agents/evolution_detector.py --dry-run    # report only, no modifications
    python agents/evolution_detector.py --dimension knowledge
    python agents/evolution_detector.py --scope check1
    python agents/evolution_detector.py --verbose
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import statistics
import sys
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Iterable

import yaml

ROOT = Path(__file__).resolve().parent.parent
WIKI_DIR = ROOT / "wiki"
OUTPUTS_DIR = ROOT / "outputs"
REPORTS_DIR = ROOT / "reports" / "evolution"
LAYER4_DIR = WIKI_DIR / "layer4"
DRIFT_DIR = LAYER4_DIR / "drift"
VERSIONS_ROOT = ROOT / "state" / "synthesis_versions"
SYNTHESIS_QUEUE = ROOT / "synthesis_queue.json"


# =============================================================================
# Namespace configuration
# =============================================================================

# Every Layer-3-bearing namespace. Extended beyond the original
# clients/knowledge/industries to include engineering + interests which
# were added after Phase 6 was drafted but use the same index.md +
# frontmatter pattern and benefit from the same evolution tracking.
NAMESPACES: list[tuple[str, Path]] = [
    ("knowledge",   WIKI_DIR / "knowledge"),
    ("industries",  WIKI_DIR / "industries"),
    ("engineering", WIKI_DIR / "engineering"),
    ("interests",   WIKI_DIR / "interests"),
]

# Clients live in a nested layout (current/former/prospects) so they
# need their own walker. Each client can have an index.md too.
CLIENT_ROOTS = ["current", "former", "prospects"]

# Used to map a dimension name into the synthesizer's versions subdir
# so archive_existing_synthesis-style backups land in the right place.
DIMENSION_TO_VERSIONS_SUBDIR = {
    "knowledge":   "topic",
    "industries":  "industry",
    "engineering": "engineering",
    "interests":   "interests",
    "clients":     "client",
}


# =============================================================================
# Frontmatter parsing and rewriting
# =============================================================================

def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Return (frontmatter_dict, body_text). Returns ({}, text) on parse errors."""
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
    """Serialize frontmatter + body back into the full file text."""
    fm_text = yaml.safe_dump(fm, sort_keys=False, default_flow_style=False, allow_unicode=True).strip()
    return f"---\n{fm_text}\n---\n{body}"


def _coerce_date(value) -> date | None:
    """Normalize a frontmatter date field into a datetime.date, or None."""
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        v = value[:10]
        try:
            return datetime.strptime(v, "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


def _now_stamp() -> str:
    """Filesystem-safe timestamp for versioned filenames."""
    return datetime.utcnow().strftime("%Y-%m-%dT%H-%M-%SZ")


def _today() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d")


# =============================================================================
# Config loading
# =============================================================================

def load_config() -> dict:
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


@dataclass
class EvolutionConfig:
    """Tunable thresholds and keyword lists for the detector."""
    stale_threshold_days: int = 180
    contradiction_keywords: list[str] = field(default_factory=list)
    platform_trigger_keywords: list[str] = field(default_factory=list)
    # Per-domain contradiction ratio thresholds from config.yaml
    sensitivity_by_domain: dict[str, float] = field(default_factory=dict)
    # Which domain_types should trigger confidence decay check
    decay_domains: set[str] = field(default_factory=lambda: {"platform-tactics", "platform-mechanics"})

    @classmethod
    def from_config(cls, config: dict) -> EvolutionConfig:
        evo = config.get("evolution", {}) or {}
        stability = config.get("domain_stability", {}) or {}
        sensitivity = {
            name: float(profile.get("change_sensitivity", 0.3))
            for name, profile in stability.items()
        }
        return cls(
            stale_threshold_days=int(evo.get("stale_threshold_days", 180)),
            # Keep keywords tight — generic words like "changed" and
            # "as of" are too common in commit messages and prose and
            # produce false positives. Prefer strong, unambiguous
            # signals that specifically describe supersession,
            # deprecation, or version transitions.
            contradiction_keywords=[k.lower() for k in evo.get("contradiction_keywords", [
                "no longer", "deprecated", "superseded by",
                "replaced by", "now requires", "breaking change",
                "no longer supported", "removed in",
            ])],
            platform_trigger_keywords=[k.lower() for k in evo.get("platform_trigger_keywords", [
                "announced", "policy change", "algorithm update",
                "new requirement", "effective ", "banned",
                "required as of", "google announced", "meta changed",
            ])],
            sensitivity_by_domain=sensitivity,
        )


# =============================================================================
# Article discovery
# =============================================================================

@dataclass
class L3Article:
    dimension: str          # "knowledge" | "industries" | "engineering" | "interests" | "clients"
    slug: str               # topic/industry/project slug (for clients: client slug)
    path: Path              # full path to index.md
    fm: dict                # parsed frontmatter
    body: str               # body text after frontmatter
    topic_dir: Path         # directory containing index.md + Layer 2 fragments

    @property
    def last_updated(self) -> date | None:
        return _coerce_date(self.fm.get("last_updated") or self.fm.get("updated"))

    @property
    def synthesis_cutoff(self) -> date | None:
        """Authoritative "when was this synthesized" timestamp for the
        purpose of detecting new-since-synthesis fragments.

        Prefers `generated_at` (injected by the synthesizer's provenance
        stamp as an ISO-8601 UTC string — always the actual synthesis
        time), then falls back to `last_updated` (which the LLM writes
        based on the latest source date, not the synthesis date —
        historically ambiguous).
        """
        gen = self.fm.get("generated_at")
        if isinstance(gen, str) and len(gen) >= 10:
            try:
                return datetime.strptime(gen[:10], "%Y-%m-%d").date()
            except ValueError:
                pass
        if isinstance(gen, (date, datetime)):
            return gen.date() if isinstance(gen, datetime) else gen
        return self.last_updated

    @property
    def domain_type(self) -> str:
        return str(self.fm.get("domain_type") or "").strip() or "strategy"

    @property
    def current_status(self) -> str:
        return str(self.fm.get("current_status") or "current").strip()

    def display_label(self) -> str:
        return f"{self.dimension}/{self.slug}"


def iter_layer3_articles(dimension_filter: str | None = None) -> Iterable[L3Article]:
    """Walk the wiki for every Layer 3 index.md across all namespaces."""
    if dimension_filter in (None, "knowledge", "industries", "engineering", "interests"):
        for dim, root in NAMESPACES:
            if dimension_filter and dim != dimension_filter:
                continue
            if not root.exists():
                continue
            for topic_dir in sorted(root.iterdir()):
                if not topic_dir.is_dir():
                    continue
                idx = topic_dir / "index.md"
                if not idx.exists():
                    continue
                art = _load_article(dim, topic_dir.name, idx, topic_dir)
                if art is not None:
                    yield art

    if dimension_filter in (None, "clients"):
        clients_root = WIKI_DIR / "clients"
        if clients_root.exists():
            for status in CLIENT_ROOTS:
                status_dir = clients_root / status
                if not status_dir.exists():
                    continue
                for client_dir in sorted(status_dir.iterdir()):
                    if not client_dir.is_dir():
                        continue
                    idx = client_dir / "index.md"
                    if not idx.exists():
                        continue
                    art = _load_article("clients", client_dir.name, idx, client_dir)
                    if art is not None:
                        yield art


def _load_article(dimension: str, slug: str, idx_path: Path, topic_dir: Path) -> L3Article | None:
    try:
        text = idx_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    fm, body = parse_frontmatter(text)
    if not fm:
        return None
    # Only Layer 3 articles get evolution tracking
    layer = fm.get("layer")
    if layer != 3:
        return None
    return L3Article(
        dimension=dimension,
        slug=slug,
        path=idx_path,
        fm=fm,
        body=body,
        topic_dir=topic_dir,
    )


def _list_fragments(topic_dir: Path) -> list[Path]:
    """Return Layer 2 fragment files within a topic directory."""
    if not topic_dir.exists():
        return []
    return [
        f for f in topic_dir.rglob("*.md")
        if f.name not in ("_index.md", "index.md", "README.md", "PLACEHOLDER.md")
    ]


def _strip_code_blocks(text: str) -> str:
    """Remove fenced code blocks (```...```) from markdown text.

    Commit-fragment files embed the raw commit message inside a
    ``` fence. Scanning the full file text gives every commit
    message a bunch of false-positive keyword hits ("changed",
    "no longer", "as of") because commits inherently describe
    changes. Stripping code blocks limits keyword scanning to
    actual prose — the part that would signal the SYNTHESIS
    is outdated, not just that a commit happened.
    """
    return re.sub(r"```[^`]*?```", "", text, flags=re.DOTALL)


def _read_fragment_date_and_text(path: Path) -> tuple[date | None, str]:
    """Return the fragment's content date + scannable body text (for
    keyword scanning). Body text has fenced code blocks stripped so
    commit-message contents don't create false-positive contradiction
    signals.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None, ""
    fm, body = parse_frontmatter(text)
    d = None
    for key in ("source_date", "updated", "created", "first_seen"):
        v = fm.get(key) if fm else None
        if v:
            d = _coerce_date(v)
            if d:
                break
    # Scan only the body prose (without frontmatter) with code blocks stripped
    scannable = _strip_code_blocks(body or text)
    return d, scannable


# =============================================================================
# The four detection checks
# =============================================================================

@dataclass
class Detection:
    check: str              # "contradiction-accumulation" | "source-divergence" | "platform-trigger" | "confidence-decay"
    article: L3Article
    signal: str             # human-readable description of what was detected
    severity: str           # "low" | "medium" | "high"
    set_status_evolving: bool = True
    set_evolution_start: bool = True
    drift_report: bool = False  # whether this triggers a wiki/layer4/drift/ file
    queue_resynthesis: bool = False
    details: dict = field(default_factory=dict)


def check_contradiction_accumulation(
    article: L3Article,
    new_fragments: list[tuple[Path, date | None, str]],
    evo_cfg: EvolutionConfig,
) -> Detection | None:
    """Check 1 — does a domain-dependent share of new fragments contain
    language signaling disagreement with established patterns?"""
    if not new_fragments:
        return None

    contradicting: list[Path] = []
    for path, _date, text in new_fragments:
        text_lower = text.lower()
        if any(kw in text_lower for kw in evo_cfg.contradiction_keywords):
            contradicting.append(path)

    if not contradicting:
        return None

    domain = article.domain_type
    threshold = evo_cfg.sensitivity_by_domain.get(domain, 0.3)
    # Regulatory (sensitivity 0.0) should trigger on ANY contradicting fragment
    if threshold == 0.0 and len(contradicting) > 0:
        ratio = 1.0
    else:
        ratio = len(contradicting) / len(new_fragments)

    if ratio < threshold and not (threshold == 0.0 and len(contradicting) > 0):
        return None

    signal = (
        f"{len(contradicting)} of {len(new_fragments)} new fragments "
        f"({int(ratio*100)}%) contain contradiction signals "
        f"(threshold {int(threshold*100)}% for domain_type={domain})"
    )
    return Detection(
        check="contradiction-accumulation",
        article=article,
        signal=signal,
        severity="medium" if ratio < 0.5 else "high",
        details={
            "contradicting_count": len(contradicting),
            "total_new": len(new_fragments),
            "ratio": round(ratio, 3),
            "threshold": threshold,
            "contradicting_paths": [str(p.relative_to(ROOT)) for p in contradicting[:10]],
        },
    )


def check_source_date_divergence(article: L3Article) -> Detection | None:
    """Check 2 — supporting_sources are old, contradicting_sources are new.

    If the average date of contradicting_sources is >6 months newer than
    the average date of supporting_sources, that's a classic evolution
    signature regardless of keyword signals.
    """
    supporting = article.fm.get("supporting_sources") or []
    contradicting = article.fm.get("contradicting_sources") or []
    if not isinstance(supporting, list) or not isinstance(contradicting, list):
        return None
    if not supporting or not contradicting:
        return None

    def _avg_date(paths: list[str]) -> date | None:
        dates: list[date] = []
        for p in paths:
            if not isinstance(p, str):
                continue
            full = ROOT / p
            d, _ = _read_fragment_date_and_text(full)
            if d:
                dates.append(d)
        if not dates:
            return None
        ordinals = [d.toordinal() for d in dates]
        return date.fromordinal(int(statistics.mean(ordinals)))

    sup_avg = _avg_date(supporting)
    con_avg = _avg_date(contradicting)
    if not sup_avg or not con_avg:
        return None

    delta_days = (con_avg - sup_avg).days
    if delta_days < 180:
        return None

    midpoint = date.fromordinal((sup_avg.toordinal() + con_avg.toordinal()) // 2)
    signal = (
        f"supporting sources avg {sup_avg}, contradicting sources avg "
        f"{con_avg} — {delta_days}-day divergence suggests pattern evolution"
    )
    return Detection(
        check="source-divergence",
        article=article,
        signal=signal,
        severity="high" if delta_days > 365 else "medium",
        details={
            "supporting_avg": sup_avg.strftime("%Y-%m-%d"),
            "contradicting_avg": con_avg.strftime("%Y-%m-%d"),
            "delta_days": delta_days,
            "detected_midpoint": midpoint.strftime("%Y-%m-%d"),
        },
    )


def check_platform_regulatory_trigger(
    article: L3Article,
    new_fragments: list[tuple[Path, date | None, str]],
    evo_cfg: EvolutionConfig,
) -> Detection | None:
    """Check 3 — any new fragment contains explicit platform-change or
    regulatory-update language. Fires regardless of contradiction ratio."""
    if not new_fragments:
        return None

    triggering: list[tuple[Path, str]] = []
    for path, _date, text in new_fragments:
        text_lower = text.lower()
        for kw in evo_cfg.platform_trigger_keywords:
            if kw in text_lower:
                triggering.append((path, kw))
                break

    if not triggering:
        return None

    signal = (
        f"{len(triggering)} new fragment(s) contain platform/regulatory "
        f"change language (e.g., '{triggering[0][1]}' in "
        f"{triggering[0][0].name})"
    )
    return Detection(
        check="platform-trigger",
        article=article,
        signal=signal,
        severity="high",
        drift_report=True,
        details={
            "triggering_count": len(triggering),
            "first_trigger_keyword": triggering[0][1],
            "triggering_paths": [str(p.relative_to(ROOT)) for p, _ in triggering[:10]],
        },
    )


def check_confidence_decay(
    article: L3Article,
    new_fragment_count: int,
    evo_cfg: EvolutionConfig,
) -> Detection | None:
    """Check 4 — old article in a fast-moving domain with no new evidence.

    Uses `synthesis_cutoff` (generated_at preferred) as the "when was
    this last touched" marker, not `last_updated`. The LLM writes
    last_updated based on the freshest source date, which for
    back-dated corpora produces a very stale-looking number even
    when the article was synthesized today. generated_at is the
    real synthesis timestamp.
    """
    cutoff = article.synthesis_cutoff
    if not cutoff:
        return None

    days_stale = (datetime.utcnow().date() - cutoff).days
    if days_stale < evo_cfg.stale_threshold_days:
        return None

    if new_fragment_count > 0:
        # Fresh evidence exists — even if it doesn't trigger check 1 or 3,
        # the article isn't decaying.
        return None

    if article.domain_type not in evo_cfg.decay_domains:
        return None

    signal = (
        f"no new evidence in {days_stale} days (since synthesis on "
        f"{cutoff.strftime('%Y-%m-%d')}) and domain_type="
        f"{article.domain_type} — knowledge may be going stale"
    )
    return Detection(
        check="confidence-decay",
        article=article,
        signal=signal,
        severity="low",
        set_status_evolving=False,  # decay doesn't mean currently evolving
        set_evolution_start=False,
        queue_resynthesis=True,
        details={
            "days_stale": days_stale,
            "domain_type": article.domain_type,
            "threshold_days": evo_cfg.stale_threshold_days,
        },
    )


# =============================================================================
# Runner — scan all articles and collect detections
# =============================================================================

def scan_article(article: L3Article, evo_cfg: EvolutionConfig) -> list[Detection]:
    """Run all four checks on a single article, return every detection."""
    detections: list[Detection] = []

    # Gather "new" fragments — those with a date newer than the
    # article's synthesis cutoff. synthesis_cutoff prefers generated_at
    # (code-injected provenance, always the actual synthesis time)
    # over last_updated (LLM-written, often the latest source date
    # instead of the synthesis date — ambiguous).
    #
    # If neither field is present we SKIP the fragment-dependent
    # checks entirely. "Every fragment is new" is not a useful
    # default — it's how Check 1 false-positives every article.
    cutoff = article.synthesis_cutoff
    fragment_files = _list_fragments(article.topic_dir)
    new_fragments: list[tuple[Path, date | None, str]] = []
    if cutoff is not None:
        for f in fragment_files:
            d, text = _read_fragment_date_and_text(f)
            if d is not None and d > cutoff:
                new_fragments.append((f, d, text))

    # Check 1 — contradiction accumulation
    det = check_contradiction_accumulation(article, new_fragments, evo_cfg)
    if det:
        detections.append(det)

    # Check 2 — source date divergence (doesn't depend on new fragments)
    det = check_source_date_divergence(article)
    if det:
        detections.append(det)

    # Check 3 — platform/regulatory trigger
    det = check_platform_regulatory_trigger(article, new_fragments, evo_cfg)
    if det:
        detections.append(det)

    # Check 4 — confidence decay
    det = check_confidence_decay(article, len(new_fragments), evo_cfg)
    if det:
        detections.append(det)

    return detections


# =============================================================================
# Mutation — apply detections to frontmatter
# =============================================================================

def _archive_existing(article: L3Article) -> Path | None:
    """Copy current index.md to state/synthesis_versions/<dim>/<slug>/<timestamp>.md
    before the detector modifies it. Mirrors the synthesizer's
    archive_existing_synthesis behavior."""
    subdir = DIMENSION_TO_VERSIONS_SUBDIR.get(article.dimension, article.dimension)
    versions_dir = VERSIONS_ROOT / subdir / article.slug
    versions_dir.mkdir(parents=True, exist_ok=True)
    archived = versions_dir / f"{_now_stamp()}.md"
    shutil.copy2(article.path, archived)
    return archived


def apply_detections(article: L3Article, detections: list[Detection], dry_run: bool) -> dict:
    """Mutate the article's frontmatter based on its detections.

    Returns a summary dict describing what would change (dry run) or
    what did change (live run).
    """
    if not detections:
        return {"modified": False}

    fm = dict(article.fm)  # work on a copy

    # Ensure evolution fields exist (backfill with defaults if absent)
    fm.setdefault("evolution_timeline", [])
    fm.setdefault("evolution_start", None)
    fm.setdefault("superseded_by", None)
    fm.setdefault("superseded_date", None)
    fm.setdefault("deprecation_notice", None)
    # contradicting_count should reflect current contradicting_sources length
    con_sources = fm.get("contradicting_sources") or []
    fm["contradicting_count"] = len(con_sources) if isinstance(con_sources, list) else 0

    today = _today()

    changes: list[str] = []

    any_evolving = any(d.set_status_evolving for d in detections)
    any_start = any(d.set_evolution_start for d in detections)

    if any_evolving and fm.get("current_status") != "evolving":
        changes.append(f"current_status: {fm.get('current_status', 'current')} → evolving")
        fm["current_status"] = "evolving"

    if any_start and not fm.get("evolution_start"):
        # Prefer the date hinted by source-divergence check if present
        start_hint = None
        for d in detections:
            if d.check == "source-divergence":
                mid = d.details.get("detected_midpoint")
                if mid:
                    start_hint = mid
                    break
        fm["evolution_start"] = start_hint or today
        changes.append(f"evolution_start: null → {fm['evolution_start']}")

    # Append one entry per detection to evolution_timeline
    timeline = fm.get("evolution_timeline") or []
    if not isinstance(timeline, list):
        timeline = []
    for d in detections:
        entry = {
            "date": today,
            "event": d.check,
            "note": d.signal,
            "severity": d.severity,
        }
        timeline.append(entry)
        changes.append(f"evolution_timeline += [{d.check}]")
    fm["evolution_timeline"] = timeline

    # Don't touch last_updated — that's owned by the synthesizer
    # and represents the synthesis date, not the detection date.

    summary = {
        "modified": True,
        "changes": changes,
        "new_status": fm.get("current_status"),
        "new_evolution_start": fm.get("evolution_start"),
    }

    if dry_run:
        summary["would_archive_to"] = str(
            (VERSIONS_ROOT / DIMENSION_TO_VERSIONS_SUBDIR.get(article.dimension, article.dimension)
             / article.slug / f"{_now_stamp()}.md").relative_to(ROOT)
        )
        return summary

    # Live write path
    archived = _archive_existing(article)
    summary["archived_to"] = str(archived.relative_to(ROOT)) if archived else None

    new_text = write_frontmatter(fm, article.body)
    article.path.write_text(new_text, encoding="utf-8")
    return summary


# =============================================================================
# Drift reports + synthesis queue integration
# =============================================================================

def write_drift_report(detection: Detection, dry_run: bool) -> Path | None:
    """For platform-trigger detections, write a Layer 4 drift file
    flagging the topic for human review and web-augmented re-synthesis."""
    if not detection.drift_report:
        return None
    DRIFT_DIR.mkdir(parents=True, exist_ok=True)
    slug = f"{detection.article.dimension}-{detection.article.slug}"
    filename = f"{slug}-{_today()}.md"
    path = DRIFT_DIR / filename
    if path.exists():
        # Don't overwrite existing drift entries for the same date
        return path
    content = (
        f"---\n"
        f"layer: 4\n"
        f"concept_type: drift\n"
        f"topics_connected: [{detection.article.dimension}/{detection.article.slug}]\n"
        f"confidence: medium\n"
        f'first_detected: "{_today()}"\n'
        f'last_updated: "{_today()}"\n'
        f"source_check: {detection.check}\n"
        f"severity: {detection.severity}\n"
        f"---\n\n"
        f"# Drift detected: {detection.article.display_label()}\n\n"
        f"**Check:** `{detection.check}`  \n"
        f"**Severity:** {detection.severity}  \n"
        f"**Signal:** {detection.signal}  \n\n"
        f"## Details\n\n"
        f"```json\n{json.dumps(detection.details, indent=2, default=str)}\n```\n\n"
        f"## Recommended action\n\n"
        f"- Review the new contradicting fragment(s) identified above\n"
        f"- Confirm the platform/regulatory change is real and material\n"
        f"- If confirmed, re-run synthesis with `--force` so the Layer 3\n"
        f"  article reflects the current state\n"
        f"- Consider adding a dated entry to the article's "
        f"`## Evolution and Change` section during re-synthesis\n"
    )
    if not dry_run:
        path.write_text(content, encoding="utf-8")
    return path


def queue_for_resynthesis(detection: Detection, dry_run: bool) -> bool:
    """For confidence-decay detections, append to synthesis_queue.json
    with priority: high. Skips if already queued."""
    if not detection.queue_resynthesis:
        return False
    if dry_run:
        return True
    if not SYNTHESIS_QUEUE.exists():
        # The queue may not exist yet — create empty
        SYNTHESIS_QUEUE.write_text("[]", encoding="utf-8")
    try:
        queue = json.loads(SYNTHESIS_QUEUE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        queue = []
    if not isinstance(queue, list):
        return False
    entry = {
        "dimension": detection.article.dimension,
        "topic": detection.article.slug,
        "priority": "high",
        "queued_by": "evolution_detector",
        "reason": detection.signal,
        "queued_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": "pending",
    }
    # Skip if already queued with same dimension+topic
    for existing in queue:
        if isinstance(existing, dict):
            if (existing.get("dimension") == entry["dimension"]
                    and existing.get("topic") == entry["topic"]
                    and existing.get("status") != "complete"):
                return False
    queue.append(entry)
    SYNTHESIS_QUEUE.write_text(json.dumps(queue, indent=2), encoding="utf-8")
    return True


# =============================================================================
# Report generation
# =============================================================================

def generate_report(
    detections_by_article: dict[str, list[Detection]],
    total_scanned: int,
    dry_run: bool,
    apply_results: dict[str, dict],
) -> str:
    """Produce the evolution-<date>.md report."""
    today = _today()
    mode = "DRY RUN" if dry_run else "APPLIED"
    lines: list[str] = [
        "# Knowledge Evolution Report",
        "",
        f"**Generated:** {today}  ",
        f"**Mode:** {mode}  ",
        f"**Articles scanned:** {total_scanned}  ",
        f"**Articles with detections:** {len(detections_by_article)}  ",
        "",
    ]

    # Bucket by check
    by_check: dict[str, list[Detection]] = {}
    for dets in detections_by_article.values():
        for d in dets:
            by_check.setdefault(d.check, []).append(d)

    if not detections_by_article:
        lines.append("No evolution signals detected. All Layer 3 articles appear stable.\n")
        return "\n".join(lines)

    # Actively evolving section (checks 1-3)
    evolving_detections = [
        d for dets in detections_by_article.values() for d in dets
        if d.set_status_evolving
    ]
    if evolving_detections:
        lines.append(f"## Actively Evolving ({len(evolving_detections)})\n")
        for d in evolving_detections:
            lines.append(f"### {d.article.display_label()}")
            lines.append(f"- **Check:** `{d.check}`")
            lines.append(f"- **Severity:** {d.severity}")
            lines.append(f"- **Signal:** {d.signal}")
            if d.drift_report:
                lines.append(f"- **Drift report:** `wiki/layer4/drift/{d.article.dimension}-{d.article.slug}-{today}.md`")
            lines.append(f"- **Recommended action:** re-synthesize with `--force`")
            lines.append("")

    # Stale / decaying section (check 4)
    decay_detections = [
        d for dets in detections_by_article.values() for d in dets
        if d.check == "confidence-decay"
    ]
    if decay_detections:
        lines.append(f"## Stale — Possible Confidence Decay ({len(decay_detections)})\n")
        lines.append(
            "_No new evidence in 6+ months in a fast-moving domain. "
            "Queued for high-priority re-synthesis with web augmentation "
            "so currency can be validated against external sources._\n"
        )
        for d in decay_detections:
            lines.append(
                f"- [[{d.article.display_label()}]] — "
                f"{d.details.get('days_stale', '?')} days stale, "
                f"domain_type={d.details.get('domain_type', '?')}"
            )
        lines.append("")

    # Per-check summary
    lines.append("## Summary by Check\n")
    for check, label in [
        ("contradiction-accumulation", "Contradiction accumulation"),
        ("source-divergence",          "Source date divergence"),
        ("platform-trigger",           "Platform/regulatory trigger"),
        ("confidence-decay",           "Confidence decay"),
    ]:
        items = by_check.get(check, [])
        lines.append(f"- **{label}:** {len(items)} article{'s' if len(items) != 1 else ''}")
    lines.append("")

    # Modifications applied
    if not dry_run:
        modified = sum(1 for r in apply_results.values() if r.get("modified"))
        lines.append(f"## Modifications Applied ({modified})\n")
        for label, result in sorted(apply_results.items()):
            if not result.get("modified"):
                continue
            lines.append(f"### {label}")
            if result.get("archived_to"):
                lines.append(f"- Archived prior version to: `{result['archived_to']}`")
            for c in result.get("changes", []):
                lines.append(f"- {c}")
            lines.append("")
    else:
        lines.append("## Dry Run — No Modifications Applied\n")
        lines.append("_Re-run without `--dry-run` to apply evolution tracking changes._\n")

    return "\n".join(lines)


# =============================================================================
# Main
# =============================================================================

def main() -> int:
    parser = argparse.ArgumentParser(description="Meridian Evolution Detector")
    parser.add_argument("--dry-run", action="store_true", help="Report only, no modifications")
    parser.add_argument("--dimension", choices=["knowledge", "industries", "engineering", "interests", "clients"], help="Limit scan to one dimension")
    parser.add_argument("--scope", choices=["all", "check1", "check2", "check3", "check4"], default="all", help="Limit to a specific check")
    parser.add_argument("--verbose", action="store_true", help="Verbose logging")
    args = parser.parse_args()

    config = load_config()
    evo_cfg = EvolutionConfig.from_config(config)

    # Collect every Layer 3 article in scope
    articles = list(iter_layer3_articles(dimension_filter=args.dimension))
    if args.verbose:
        print(f"Scanning {len(articles)} Layer 3 articles...", file=sys.stderr)

    detections_by_article: dict[str, list[Detection]] = {}
    apply_results: dict[str, dict] = {}

    allowed_checks = {
        "all": {"contradiction-accumulation", "source-divergence", "platform-trigger", "confidence-decay"},
        "check1": {"contradiction-accumulation"},
        "check2": {"source-divergence"},
        "check3": {"platform-trigger"},
        "check4": {"confidence-decay"},
    }[args.scope]

    for article in articles:
        try:
            detections = scan_article(article, evo_cfg)
        except Exception as e:
            print(f"ERROR scanning {article.display_label()}: {e}", file=sys.stderr)
            continue
        detections = [d for d in detections if d.check in allowed_checks]
        if not detections:
            continue

        label = article.display_label()
        detections_by_article[label] = detections

        if args.verbose:
            for d in detections:
                print(f"  {label}: {d.check} — {d.signal}", file=sys.stderr)

        # Side effects: drift reports + synthesis queue
        for d in detections:
            if d.drift_report:
                write_drift_report(d, args.dry_run)
            if d.queue_resynthesis:
                queue_for_resynthesis(d, args.dry_run)

        # Apply frontmatter mutations (or preview them)
        apply_results[label] = apply_detections(article, detections, args.dry_run)

    # Write the report
    # Write to reports/ (git-tracked, dashboard-browsable) and outputs/ (legacy)
    report = generate_report(detections_by_article, len(articles), args.dry_run, apply_results)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS_DIR / f"evolution-{_today()}.md"
    report_path.write_text(report, encoding="utf-8")
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUTS_DIR / f"evolution-{_today()}.md").write_text(report, encoding="utf-8")

    print(json.dumps({
        "status": "ok",
        "mode": "dry-run" if args.dry_run else "live",
        "articles_scanned": len(articles),
        "articles_with_detections": len(detections_by_article),
        "report_path": str(report_path.relative_to(ROOT)),
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
