#!/usr/bin/env python3
"""Meridian Web UI — dashboard and search interface for the wiki.

Replaces Obsidian as the primary way to browse and interact with Meridian.
Reads directly from /meridian/ filesystem. Calls receiver API for agent actions.
"""

import json
import os
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import markdown
from flask import Flask, Response, jsonify, render_template, request, send_file
import requests
import yaml


def get_md():
    """Create a fresh Markdown converter."""
    return markdown.Markdown(extensions=[
        "extra",
        "codehilite",
        "toc",
        "nl2br",
        "sane_lists",
    ])


def process_citations(text: str) -> tuple[str, list[dict]]:
    """Convert [[source1, source2]] inline citations to footnote numbers."""
    citations = []
    citation_map = {}
    counter = [1]

    def clean_name(s: str) -> str:
        s = s.strip().replace(".md", "").replace(".MD", "")
        s = s.split("/")[-1]  # take filename only
        s = s.replace("-", " ").replace("_", " ")
        return s.title()

    def replace_citation(match):
        raw = match.group(1)
        sources = [s.strip() for s in raw.split(",")]

        # Heuristic: citations have commas (multiple sources) or .md suffixes.
        # Single items without .md are wikilinks — leave for wikilink converter.
        has_comma = "," in raw
        has_md = ".md" in raw.lower()
        if not has_comma and not has_md:
            # Single wikilink, not a citation — leave for wikilink converter
            return match.group(0)

        key = "|".join(sorted(sources))
        if key in citation_map:
            num = citation_map[key]
        else:
            num = counter[0]
            citation_map[key] = num
            citations.append({
                "number": num,
                "sources": sources,
                "display_names": [clean_name(s) for s in sources],
            })
            counter[0] += 1

        return (
            f'<sup class="citation" id="cite-{num}">'
            f'<a href="#source-{num}">[{num}]</a>'
            f"</sup>"
        )

    processed = re.sub(r"\[\[([^\]]+)\]\]", replace_citation, text)
    return processed, citations


def build_sources_html(
    citations: list[dict],
    topic_context: dict | None = None,
) -> str:
    """Build footnote-style citations section.

    Called "Citations" (not "Sources") because it only shows fragments
    actually cited inline in the synthesis body — not the full set of
    fragments under the topic. When `topic_context` is provided, the
    header includes a "N cited of M total fragments" framing and a link
    to browse the full fragment list.

    topic_context, if set, should contain:
        slug: str
        display_name: str
        total_fragments: int
    """
    if not citations:
        return ""

    header_lines = ['<div class="sources-section">', "<h2>Citations</h2>"]
    if topic_context:
        slug = topic_context.get("slug", "")
        name = topic_context.get("display_name", slug)
        total = topic_context.get("total_fragments", 0)
        cited = len(citations)
        header_lines.append(
            f'<p class="sources-subhead">'
            f'{cited} fragment{"" if cited == 1 else "s"} cited inline. '
            f'<a href="/topic/{slug}">Browse all {total} fragments in {name} &rarr;</a>'
            f"</p>"
        )

    lines = header_lines + ['<ol class="sources-list">']
    for cite in citations:
        lines.append(f'<li id="source-{cite["number"]}">')
        links = []
        for source, display in zip(cite["sources"], cite["display_names"]):
            slug = source.strip().replace(".md", "")
            if not slug.startswith("wiki/"):
                slug = f"wiki/{slug}"
            links.append(f'<a href="/article/{slug}.md" class="source-link">{display}</a>')
        lines.append(" &middot; ".join(links))
        # Back-link to the citation in the body so readers can return
        # to where they were reading without scrolling.
        lines.append(
            f' <a href="#cite-{cite["number"]}" class="source-backref" '
            f'aria-label="Back to citation {cite["number"]}">&#8617;</a>'
        )
        lines.append("</li>")
    lines.append("</ol></div>")
    return "\n".join(lines)


def convert_wikilinks(text: str) -> str:
    """Convert remaining [[wikilinks]] to clickable HTML links."""
    def replace_link(match):
        full = match.group(1)
        if "|" in full:
            target, display = full.split("|", 1)
        else:
            target = full
            display = target.split("/")[-1].replace("-", " ").title()
        if not target.startswith("wiki/"):
            target = f"wiki/{target}"
        if not target.endswith(".md"):
            target += ".md"
        return f"[{display}](/article/{target})"
    return re.sub(r"\[\[([^\]]+)\]\]", replace_link, text)


def _split_related_topics(body: str) -> tuple[str, str]:
    """Split the article body at the `## Related Topics` header.

    Returns (body_before, related_topics_and_after). If the header is
    missing, the whole body is treated as "before" and the second value
    is empty. Case-insensitive match so a lowercase or mixed-case header
    still splits correctly.

    This exists because the Related Topics section is a bulleted list of
    bare wikilinks like `[[wiki/knowledge/seo/index.md]]`. The citation
    processor, which runs first, would otherwise turn each of those into
    a numbered footnote (they contain `.md`), and the rendered article
    would show a list of numbers with no text at the bottom.
    """
    m = re.search(r"(?mi)^##\s+Related\s+Topics\s*$", body)
    if not m:
        return body, ""
    return body[: m.start()], body[m.start():]


def render_markdown(body: str, topic_context: dict | None = None) -> str:
    """Convert markdown body to HTML with citation footnotes and wikilinks.

    `topic_context` is an optional dict passed from view_article when the
    article is a Level 3 synthesis. It lets the citations footer show
    "N cited of M total" and link to the full fragment browser.
    """
    # Step 1: Separate the Related Topics section so its wikilinks are not
    # mistaken for citations.
    body_before, related = _split_related_topics(body)

    # Step 2: Process citations on the analytical body only.
    body_before, citations = process_citations(body_before)

    # Step 3: Convert wikilinks in both segments.
    body_before = convert_wikilinks(body_before)
    related = convert_wikilinks(related)

    # Step 4: Render markdown to HTML.
    md = get_md()
    article_html = md.convert(body_before + "\n\n" + related)

    # Step 5: Append citations section (footnote-style, topic-aware).
    sources_html = build_sources_html(citations, topic_context=topic_context)
    return article_html + sources_html

app = Flask(__name__)

MERIDIAN_ROOT = Path(os.environ.get("MERIDIAN_ROOT", "/meridian"))
WIKI_DIR = MERIDIAN_ROOT / "wiki"
RAW_DIR = MERIDIAN_ROOT / "raw"
CAPTURE_DIR = MERIDIAN_ROOT / "capture"
CLIENTS_YAML = MERIDIAN_ROOT / "clients.yaml"
TOPICS_YAML = MERIDIAN_ROOT / "topics.yaml"
ENGINEERING_TOPICS_YAML = MERIDIAN_ROOT / "engineering-topics.yaml"
PROJECTS_YAML = MERIDIAN_ROOT / "projects.yaml"
ENGINEERING_DIR = WIKI_DIR / "engineering"
COMMITS_CAPTURE_DIR = CAPTURE_DIR / "external" / "commits"
RECEIVER_URL = os.environ.get("MERIDIAN_RECEIVER_URL", "http://localhost:8000")
RECEIVER_TOKEN = os.environ.get("MERIDIAN_RECEIVER_TOKEN", "")


# ---------------------------------------------------------------------------
# Registry lookups — loaded once at startup, refreshed on demand via reload.
# ---------------------------------------------------------------------------

def _load_client_names() -> dict:
    """Map client slug -> display name. Empty if clients.yaml is missing."""
    if not CLIENTS_YAML.exists():
        return {}
    try:
        data = yaml.safe_load(CLIENTS_YAML.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return {}
    lookup: dict[str, str] = {}
    for entry in data.get("clients", []):
        slug = (entry.get("slug") or "").strip()
        name = (entry.get("name") or "").strip()
        if slug and name:
            lookup[slug] = name
    return lookup


def _load_topic_names() -> dict:
    """Map topic slug -> display name. Falls back to title-cased slug."""
    if not TOPICS_YAML.exists():
        return {}
    try:
        data = yaml.safe_load(TOPICS_YAML.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return {}
    lookup: dict[str, str] = {}
    for entry in data.get("topics", []):
        slug = (entry.get("slug") or "").strip()
        name = (entry.get("name") or "").strip()
        if slug and name:
            lookup[slug] = name
    return lookup


def _load_engineering_topic_names() -> dict:
    """Map engineering topic slug -> display name. Reads engineering-topics.yaml."""
    if not ENGINEERING_TOPICS_YAML.exists():
        return {}
    try:
        data = yaml.safe_load(ENGINEERING_TOPICS_YAML.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return {}
    lookup: dict[str, str] = {}
    for entry in data.get("topics", []):
        slug = (entry.get("slug") or "").strip()
        name = (entry.get("name") or "").strip()
        if slug and name:
            lookup[slug] = name
    return lookup


def _load_projects() -> list[dict]:
    """Return the list of project dicts from projects.yaml. Empty list if missing."""
    if not PROJECTS_YAML.exists():
        return []
    try:
        data = yaml.safe_load(PROJECTS_YAML.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return []
    return data.get("projects", [])


CLIENT_NAMES: dict = _load_client_names()
TOPIC_NAMES: dict = _load_topic_names()
ENGINEERING_TOPIC_NAMES: dict = _load_engineering_topic_names()
PROJECTS: list = _load_projects()


def client_display_name(slug_or_name: str) -> str:
    """Resolve a client slug to its display name, or return the input unchanged."""
    if not slug_or_name:
        return ""
    key = slug_or_name.strip().lower()
    return CLIENT_NAMES.get(key, slug_or_name)


def receiver_headers():
    return {"Authorization": f"Bearer {RECEIVER_TOKEN}", "Content-Type": "application/json"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_frontmatter(content: str) -> tuple[dict, str]:
    """Parse YAML frontmatter, return (metadata, body)."""
    if not content.startswith("---"):
        return {}, content
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content
    try:
        fm = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        fm = {}
    return fm, parts[2].strip()


def read_article(path: Path) -> dict:
    """Read a wiki article and return structured data."""
    content = path.read_text(encoding="utf-8", errors="replace")
    fm, body = parse_frontmatter(content)
    word_count = len(body.split())
    return {
        "path": str(path.relative_to(MERIDIAN_ROOT)),
        "filename": path.name,
        "title": fm.get("title", path.stem),
        "type": fm.get("type", ""),
        "layer": fm.get("layer", ""),
        "tags": fm.get("tags", []),
        "created": fm.get("created", ""),
        "updated": fm.get("updated", ""),
        "client_source": fm.get("client_source", ""),
        "industry_context": fm.get("industry_context", ""),
        "word_count": word_count,
        "body": body,
        "frontmatter": fm,
    }


def get_stats() -> dict:
    """Get wiki statistics.

    After the engineering namespace was added, `wiki_total` now reports
    business-domain content only (knowledge + industries + clients +
    articles + concepts). Engineering articles are counted separately
    under `engineering_fragments` so the dashboard cards don't conflate
    two unrelated knowledge streams. `capture_total` is recursive so
    commit fragments waiting to be classified are visible too.
    """
    stats = {
        "wiki_total": 0,           # business domain only (excludes engineering)
        "articles": 0,
        "concepts": 0,
        "knowledge": 0,
        "clients_current": 0,
        "clients_former": 0,
        "raw": 0,
        "capture": 0,              # top-level capture/ only (original meaning)
        "capture_total": 0,        # recursive, includes commit fragments
        "capture_commits_queue": 0,  # unclassified commit fragments waiting
        "knowledge_topics": 0,
        "client_folders": 0,
        "industries": 0,
        "industries_with_content": 0,
        # === Engineering namespace ===
        "engineering_topics_registered": 0,
        "engineering_topics_with_fragments": 0,
        "engineering_fragments": 0,
        "engineering_l3": 0,
        "projects_registered": 0,
        "projects_active": 0,
        "commits_ingested_total": 0,
    }
    if WIKI_DIR.exists():
        # Count business-domain wiki entries only. Engineering is excluded
        # from wiki_total so the headline number reflects a coherent set.
        business_count = 0
        for sub in ("knowledge", "industries", "clients", "articles", "concepts"):
            d = WIKI_DIR / sub
            if d.exists():
                business_count += sum(1 for _ in d.rglob("*.md"))
        stats["wiki_total"] = business_count

        articles_dir = WIKI_DIR / "articles"
        if articles_dir.exists():
            stats["articles"] = sum(1 for _ in articles_dir.glob("*.md"))
        concepts_dir = WIKI_DIR / "concepts"
        if concepts_dir.exists():
            stats["concepts"] = sum(1 for _ in concepts_dir.glob("*.md"))
        knowledge_dir = WIKI_DIR / "knowledge"
        if knowledge_dir.exists():
            stats["knowledge"] = sum(1 for _ in knowledge_dir.rglob("*.md")
                                     if _.name != "_index.md")
            stats["knowledge_topics"] = sum(1 for d in knowledge_dir.iterdir()
                                            if d.is_dir())
        clients_dir = WIKI_DIR / "clients"
        if clients_dir.exists():
            current = clients_dir / "current"
            if current.exists():
                stats["clients_current"] = sum(1 for d in current.iterdir() if d.is_dir())
                stats["client_folders"] = stats["clients_current"]
            former = clients_dir / "former"
            if former.exists():
                stats["clients_former"] = sum(1 for d in former.iterdir() if d.is_dir())
        # Industries dimension (third axis alongside clients + knowledge)
        industries_dir = WIKI_DIR / "industries"
        if industries_dir.exists():
            industry_dirs = [d for d in industries_dir.iterdir() if d.is_dir()]
            stats["industries"] = len(industry_dirs)
            # "With content" = more than just PLACEHOLDER.md
            stats["industries_with_content"] = sum(
                1
                for d in industry_dirs
                if any(
                    f.name not in ("PLACEHOLDER.md", "index.md")
                    for f in d.glob("*.md")
                )
            )
        # Engineering namespace
        if ENGINEERING_DIR.exists():
            eng_fragments = [
                f for f in ENGINEERING_DIR.rglob("*.md")
                if f.name not in ("README.md", "_index.md")
            ]
            # Exclude index.md (Layer 3 syntheses) from the raw fragment count
            stats["engineering_fragments"] = sum(
                1 for f in eng_fragments if f.name != "index.md"
            )
            stats["engineering_l3"] = sum(
                1 for f in eng_fragments if f.name == "index.md"
            )
            topic_dirs = [d for d in ENGINEERING_DIR.iterdir() if d.is_dir()]
            stats["engineering_topics_with_fragments"] = sum(
                1 for d in topic_dirs
                if any(f.name not in ("README.md", "_index.md", "index.md")
                       for f in d.glob("*.md"))
            )
    # Engineering + project registry counts (drive from registry files,
    # not filesystem, so empty topics still show as registered)
    stats["engineering_topics_registered"] = len(ENGINEERING_TOPIC_NAMES)
    stats["projects_registered"] = len(PROJECTS)
    stats["projects_active"] = sum(
        1 for p in PROJECTS if p.get("status") == "active"
    )

    if RAW_DIR.exists():
        stats["raw"] = sum(1 for _ in RAW_DIR.glob("*.md") if _.name != "_index.md")
    if CAPTURE_DIR.exists():
        stats["capture"] = sum(1 for _ in CAPTURE_DIR.glob("*.md"))
        stats["capture_total"] = sum(1 for _ in CAPTURE_DIR.rglob("*.md"))
    if COMMITS_CAPTURE_DIR.exists():
        stats["capture_commits_queue"] = sum(
            1 for _ in COMMITS_CAPTURE_DIR.rglob("*.md")
        )
        # Total commits ingested = unclassified (in capture/) + classified (moved to wiki/engineering/)
        stats["commits_ingested_total"] = (
            stats["capture_commits_queue"] + stats["engineering_fragments"]
        )
    return stats


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def dashboard():
    stats = get_stats()

    # Recent activity from log
    log_path = WIKI_DIR / "log.md"
    recent_log = []
    if log_path.exists():
        content = log_path.read_text(encoding="utf-8", errors="replace")
        entries = re.findall(r"## \[(\d{4}-\d{2}-\d{2})\] (.+)", content)
        recent_log = [{"date": d, "entry": e} for d, e in entries[-10:]]
        recent_log.reverse()

    # Client list — count topics each client contributed insights to
    clients = []
    clients_dir = WIKI_DIR / "clients" / "current"
    if clients_dir.exists():
        for d in sorted(clients_dir.iterdir()):
            if d.is_dir():
                # Read _index.md and count "What We Learned" links
                topic_count = 0
                insight_count = 0
                index_file = d / "_index.md"
                if index_file.exists():
                    try:
                        content = index_file.read_text(encoding="utf-8", errors="replace")
                        # Count wikilinks to knowledge/ topics
                        topic_links = re.findall(r"\[\[knowledge/[^|\]]+", content)
                        topic_count = len(set(topic_links))
                        # Sum insight counts in parentheses: "(N insights)"
                        insight_nums = re.findall(r"\((\d+)\s+insights?\)", content)
                        insight_count = sum(int(n) for n in insight_nums)
                    except Exception:
                        pass
                clients.append({
                    "slug": d.name,
                    "articles": topic_count,
                    "insights": insight_count,
                })
        # Sort by insight count descending
        clients.sort(key=lambda x: -x["insights"])

    # Knowledge topics with Layer 3 detection
    topics = []
    knowledge_dir = WIKI_DIR / "knowledge"
    if knowledge_dir.exists():
        for d in sorted(knowledge_dir.iterdir()):
            if d.is_dir():
                article_count = sum(1 for _ in d.rglob("*.md")
                                    if _.name not in ("_index.md", "index.md"))
                topic_data = {"slug": d.name, "articles": article_count,
                              "layer3": False, "confidence": "", "evidence_count": 0,
                              "last_updated": ""}
                # Check for Layer 3 synthesis
                index_file = d / "index.md"
                if index_file.exists():
                    try:
                        content = index_file.read_text(encoding="utf-8", errors="replace")
                        if "layer: 3" in content:
                            topic_data["layer3"] = True
                            fm, _ = parse_frontmatter(content)
                            topic_data["confidence"] = fm.get("confidence", "")
                            topic_data["evidence_count"] = fm.get("evidence_count", 0)
                            topic_data["last_updated"] = fm.get("last_updated", "")
                    except Exception:
                        pass
                topics.append(topic_data)
        # Sort: Layer 3 first, then by article count
        topics.sort(key=lambda x: (not x["layer3"], -x["articles"]))

    # Industries — third knowledge dimension
    industries = []
    industries_yaml_path = MERIDIAN_ROOT / "industries.yaml"
    industry_name_by_slug: dict[str, str] = {}
    if industries_yaml_path.exists():
        try:
            data = yaml.safe_load(industries_yaml_path.read_text(encoding="utf-8")) or {}
            for entry in data.get("industries", []):
                if isinstance(entry, dict) and entry.get("slug"):
                    industry_name_by_slug[entry["slug"]] = entry.get("name", entry["slug"])
        except yaml.YAMLError:
            pass

    industries_dir = WIKI_DIR / "industries"
    if industries_dir.exists():
        for d in sorted(industries_dir.iterdir()):
            if not d.is_dir():
                continue
            real_fragments = [
                f
                for f in d.glob("*.md")
                if f.name not in ("_index.md", "index.md", "PLACEHOLDER.md")
            ]
            is_placeholder = (
                not real_fragments and (d / "PLACEHOLDER.md").exists()
            )
            industry = {
                "slug": d.name,
                "name": industry_name_by_slug.get(d.name, d.name.replace("-", " ").title()),
                "fragment_count": len(real_fragments),
                "layer3": False,
                "placeholder": is_placeholder,
            }
            index_file = d / "index.md"
            if index_file.exists():
                try:
                    content = index_file.read_text(encoding="utf-8", errors="replace")
                    if "layer: 3" in content:
                        industry["layer3"] = True
                except Exception:
                    pass
            industries.append(industry)
        # Sort: Layer 3 first, then by fragment count descending, placeholders last
        industries.sort(
            key=lambda x: (x["placeholder"], not x["layer3"], -x["fragment_count"])
        )

    # Synthesis queue status
    synth_status = {"pending": 0, "running": 0, "complete": 0, "failed": 0}
    layer3_count = 0
    try:
        if RECEIVER_TOKEN:
            resp = requests.get(f"{RECEIVER_URL}/synthesize/queue", timeout=5)
            if resp.status_code == 200:
                synth_status = resp.json()
    except Exception:
        pass

    # Count Layer 3 articles, total insights, cross-client topics
    total_insights = 0
    cross_client_topics = 0
    knowledge_dir = WIKI_DIR / "knowledge"
    if knowledge_dir.exists():
        for idx in knowledge_dir.rglob("index.md"):
            try:
                content = idx.read_text(encoding="utf-8", errors="replace")
                if "layer: 3" in content:
                    layer3_count += 1
            except Exception:
                pass
        # Count insights in client-extractions files
        for ext_file in knowledge_dir.rglob("client-extractions.md"):
            try:
                content = ext_file.read_text(encoding="utf-8", errors="replace")
                # Count bullet items (each line starting with "- ")
                insights = sum(1 for line in content.split("\n") if line.strip().startswith("- "))
                total_insights += insights
                # Check if multiple clients cited (distinct [[Name, date]] patterns)
                import re as _re
                clients_cited = set(_re.findall(r"\[\[([^,]+),", content))
                if len(clients_cited) >= 3:
                    cross_client_topics += 1
            except Exception:
                pass

    # Synthesis coverage
    #
    # The denominator is the set of topics we ACTUALLY intend to
    # synthesize — i.e. entries in synthesis_queue.json excluding any
    # that are flagged `skip`. Using a raw filesystem count of
    # wiki/knowledge/* inflates the denominator with off-registry
    # legacy directories and makes the metric lie about actual
    # coverage. The numerator is topics from that same set that now
    # carry a completed Layer 3 synthesis marker.
    synthesis_coverage = 0
    queue_path = MERIDIAN_ROOT / "synthesis_queue.json"
    if queue_path.exists():
        try:
            queue_items = json.loads(queue_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            queue_items = []
        intended = {
            item.get("topic")
            for item in queue_items
            if item.get("status") != "skip" and item.get("topic")
        }
        if intended:
            completed = sum(
                1
                for topic in intended
                if (WIKI_DIR / "knowledge" / topic / "index.md").exists()
                and "layer: 3"
                in (WIKI_DIR / "knowledge" / topic / "index.md").read_text(
                    encoding="utf-8", errors="replace"
                )
            )
            synthesis_coverage = round(completed / len(intended) * 100)
    elif stats.get("knowledge_topics", 0) > 0:
        # Fallback: pre-queue legacy path
        synthesis_coverage = round(layer3_count / stats["knowledge_topics"] * 100)

    # Pipeline freshness — find latest log entries by operation type
    pipeline = {"capture": "", "distill": "", "compile": "", "synthesize": "", "lint": ""}
    if log_path.exists():
        log_content = log_path.read_text(encoding="utf-8", errors="replace")
        for op in pipeline.keys():
            matches = re.findall(rf"## \[(\d{{4}}-\d{{2}}-\d{{2}})\] {op}", log_content)
            if matches:
                pipeline[op] = max(matches)

    # Active jobs from receiver
    active_jobs = 0
    try:
        if RECEIVER_TOKEN:
            resp = requests.get(f"{RECEIVER_URL}/jobs", headers=receiver_headers(), timeout=3)
            # Endpoint may not exist — fallback gracefully
    except Exception:
        pass

    # Synthesis running detection — check queue for running status
    synth_running = synth_status.get("running", 0) > 0

    # Latest lint report — find the most recent wiki/articles/lint-*.md so the
    # dashboard can link straight to it from the Pipeline Freshness card. The
    # filename carries the date (lint-YYYY-MM-DD.md), so a sorted glob picks
    # the latest deterministically without parsing frontmatter.
    latest_lint_report: dict | None = None
    lint_reports_dir = WIKI_DIR / "articles"
    if lint_reports_dir.exists():
        lint_files = sorted(lint_reports_dir.glob("lint-*.md"))
        if lint_files:
            latest = lint_files[-1]
            try:
                rel_path = latest.relative_to(MERIDIAN_ROOT).as_posix()
            except ValueError:
                rel_path = ""
            # Extract YYYY-MM-DD from filename `lint-YYYY-MM-DD.md`
            m = re.match(r"lint-(\d{4}-\d{2}-\d{2})\.md$", latest.name)
            latest_lint_report = {
                "path": rel_path,
                "date": m.group(1) if m else latest.stem,
            }

    metrics = {
        "total_insights": total_insights,
        "cross_client_topics": cross_client_topics,
        "synthesis_coverage": synthesis_coverage,
        "pipeline": pipeline,
        "synth_running": synth_running,
        "latest_lint_report": latest_lint_report,
    }

    # Engineering topics — list from registry, enriched with fragment counts
    engineering_topics = _load_engineering_topics_with_counts()
    # Projects — list from registry, enriched with fragment counts
    projects = _load_projects_with_counts()

    return render_template("dashboard.html",
                           stats=stats, recent_log=recent_log,
                           clients=clients, topics=topics, industries=industries,
                           engineering_topics=engineering_topics,
                           projects=projects,
                           synth_status=synth_status, layer3_count=layer3_count,
                           metrics=metrics)


def _load_engineering_topics_with_counts() -> list[dict]:
    """Return engineering topics from registry, enriched with fragment counts
    and Layer 3 status from the filesystem."""
    result = []
    if not ENGINEERING_TOPICS_YAML.exists():
        return result
    try:
        data = yaml.safe_load(ENGINEERING_TOPICS_YAML.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return result
    for entry in data.get("topics", []):
        if not isinstance(entry, dict):
            continue
        slug = entry.get("slug", "")
        if not slug:
            continue
        topic_dir = ENGINEERING_DIR / slug
        fragment_count = 0
        has_l3 = False
        if topic_dir.exists():
            fragment_count = sum(
                1 for f in topic_dir.glob("*.md")
                if f.name not in ("README.md", "_index.md", "index.md")
            )
            if (topic_dir / "index.md").exists():
                try:
                    idx = (topic_dir / "index.md").read_text(encoding="utf-8", errors="replace")
                    if "layer: 3" in idx:
                        has_l3 = True
                except Exception:
                    pass
        result.append({
            "slug": slug,
            "name": entry.get("name", slug),
            "category": entry.get("category", ""),
            "fragment_count": fragment_count,
            "layer3": has_l3,
        })
    # Sort: L3 first, then by fragment count descending
    result.sort(key=lambda x: (not x["layer3"], -x["fragment_count"]))
    return result


def _load_projects_with_counts() -> list[dict]:
    """Return projects from registry, enriched with commit fragment counts."""
    result = []
    for p in PROJECTS:
        slug = p.get("slug", "")
        if not slug:
            continue
        # Count commits that have been ingested (capture + wiki/engineering combined)
        commits_capture = 0
        capture_dir = COMMITS_CAPTURE_DIR / slug
        if capture_dir.exists():
            commits_capture = sum(1 for _ in capture_dir.glob("*.md"))
        commits_classified = 0
        if ENGINEERING_DIR.exists():
            # Classified fragments are prefixed with project slug in filename
            commits_classified = sum(
                1
                for f in ENGINEERING_DIR.rglob(f"{slug}-*.md")
                if f.name not in ("README.md", "_index.md", "index.md")
            )
        result.append({
            "slug": slug,
            "name": p.get("name", slug),
            "description": p.get("description", ""),
            "status": p.get("status", ""),
            "stack": p.get("stack", []),
            "commits_queue": commits_capture,
            "commits_classified": commits_classified,
            "commits_total": commits_capture + commits_classified,
        })
    # Sort: active first, then by total commits descending
    status_order = {"active": 0, "dormant": 1, "archived": 2}
    result.sort(key=lambda x: (status_order.get(x["status"], 9), -x["commits_total"]))
    return result


@app.route("/search")
def search():
    query = request.args.get("q", "").strip()
    if not query:
        return render_template("search.html", query="", results=[])

    terms = [t.lower() for t in query.split() if len(t) > 2]
    results = []

    for md_file in WIKI_DIR.rglob("*.md"):
        if md_file.name in ("_index.md", "_backlinks.md", "log.md", "home.md"):
            continue
        try:
            content = md_file.read_text(encoding="utf-8", errors="replace")
            content_lower = content.lower()
            score = sum(content_lower.count(term) for term in terms)
            if score > 0:
                fm, body = parse_frontmatter(content)
                excerpt = body[:300].replace("\n", " ")
                results.append({
                    "path": str(md_file.relative_to(MERIDIAN_ROOT)),
                    "title": fm.get("title", md_file.stem),
                    "score": score,
                    "excerpt": excerpt,
                    "type": fm.get("type", ""),
                })
        except Exception:
            continue

    results.sort(key=lambda x: x["score"], reverse=True)
    return render_template("search.html", query=query, results=results[:50])


# ---------------------------------------------------------------------------
# Taxonomy review queue — surface clients and industries that need human input
# ---------------------------------------------------------------------------

def _parse_clients_yaml_for_review() -> list[dict]:
    """Parse clients.yaml line-by-line so we can capture the `# classifier: X`
    confidence comments that PyYAML would throw away. Returns a list of
    client dicts with the extra review metadata attached.
    """
    path = MERIDIAN_ROOT / "clients.yaml"
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()

    rows: list[dict] = []
    current: dict | None = None
    for line in lines:
        m = re.match(r"^\s*-\s+name:\s*\"?([^\"]+)\"?", line)
        if m:
            if current:
                rows.append(current)
            current = {
                "name": m.group(1).strip(),
                "slug": "",
                "status": "",
                "industry": "",
                "classifier": "",
            }
            continue
        if current is None:
            continue
        m = re.match(r"^\s*slug:\s*(\S+)", line)
        if m:
            current["slug"] = m.group(1).strip()
            continue
        m = re.match(r"^\s*industry:\s*([a-z0-9-]+)(?:\s*#\s*classifier:\s*(\w+))?", line)
        if m:
            current["industry"] = m.group(1)
            if m.group(2):
                current["classifier"] = m.group(2)
            continue
        m = re.match(r"^\s*status:\s*(\S+)", line)
        if m:
            current["status"] = m.group(1).strip()
            continue
    if current:
        rows.append(current)
    return rows


def _rewrite_client_industry(slug: str, new_industry: str) -> bool:
    """Surgically rewrite one client's industry line in clients.yaml.

    Returns True if the file was updated. Idempotent — no-op if the
    slug already has the requested industry. Comment tag is updated
    to `# classifier: manual` so the UI no longer shows the row as
    a review candidate.
    """
    path = MERIDIAN_ROOT / "clients.yaml"
    if not path.exists():
        return False
    lines = path.read_text(encoding="utf-8").splitlines()

    out: list[str] = []
    i = 0
    touched = False
    target_slug = slug.strip()
    in_target = False
    while i < len(lines):
        line = lines[i]
        slug_m = re.match(r"^(\s*)slug:\s*(\S+)", line)
        if slug_m:
            in_target = slug_m.group(2).strip() == target_slug
            out.append(line)
            # Look ahead for an existing industry line under this entry;
            # replace it, or insert one if the client has none yet.
            j = i + 1
            inserted = False
            while j < len(lines) and not re.match(r"^\s*-\s+name:", lines[j]):
                ind_m = re.match(r"^(\s*)industry:\s*\S+", lines[j])
                if in_target and ind_m:
                    lines[j] = f"{ind_m.group(1)}industry: {new_industry}  # classifier: manual"
                    touched = True
                    inserted = True
                    break
                j += 1
            if in_target and not inserted:
                indent = slug_m.group(1)
                out.append(f"{indent}industry: {new_industry}  # classifier: manual")
                touched = True
            i += 1
            continue
        out.append(line)
        i += 1

    if touched:
        final = "\n".join(out)
        if not final.endswith("\n"):
            final += "\n"
        path.write_text(final, encoding="utf-8")
    return touched


@app.route("/review/taxonomy", methods=["GET", "POST"])
def review_taxonomy():
    """Review queue for the taxonomy registries.

    GET  renders the review page.
    POST accepts a form submission of slug=<client>&industry=<industry>
    and rewrites clients.yaml in place (gitignored file — the dashboard
    container bind-mounts /meridian so this write is durable and
    survives deploys).
    """
    message = ""
    if request.method == "POST":
        slug = (request.form.get("slug") or "").strip()
        industry = (request.form.get("industry") or "").strip()
        if slug and industry:
            if _rewrite_client_industry(slug, industry):
                message = f"Assigned {industry} to {slug}"
            else:
                message = f"No change — {slug} already has this industry or was not found"

    rows = _parse_clients_yaml_for_review()

    # Load the list of valid industries from industries.yaml
    industries_for_picker: list[dict] = []
    industries_yaml_path = MERIDIAN_ROOT / "industries.yaml"
    if industries_yaml_path.exists():
        try:
            data = yaml.safe_load(industries_yaml_path.read_text(encoding="utf-8")) or {}
            for entry in data.get("industries", []):
                if isinstance(entry, dict) and entry.get("slug"):
                    industries_for_picker.append(
                        {"slug": entry["slug"], "name": entry.get("name", entry["slug"])}
                    )
        except yaml.YAMLError:
            pass

    # Classify rows into review buckets
    needs_review = [
        r
        for r in rows
        if r["classifier"] == "low" or not r["industry"]
    ]
    needs_review.sort(key=lambda r: (r["classifier"] != "low", r["slug"]))

    ok_rows = [r for r in rows if r["classifier"] in ("high", "manual")]

    # Industries with no fragment content (placeholder-only)
    empty_industries: list[str] = []
    industries_dir = WIKI_DIR / "industries"
    if industries_dir.exists():
        for d in sorted(industries_dir.iterdir()):
            if not d.is_dir():
                continue
            real = [
                f
                for f in d.glob("*.md")
                if f.name not in ("index.md", "_index.md", "PLACEHOLDER.md")
            ]
            if not real:
                empty_industries.append(d.name)

    return render_template(
        "review_taxonomy.html",
        needs_review=needs_review,
        ok_count=len(ok_rows),
        total=len(rows),
        industries=industries_for_picker,
        empty_industries=empty_industries,
        message=message,
    )


def _topic_context_for(filepath: Path) -> dict | None:
    """If `filepath` lives under wiki/knowledge/<slug>/, return a context
    dict with the slug, display name, and total fragment count.

    Used for:
      - Level 3 synthesis pages (`index.md`): threaded into the citations
        footer so it can show "N of M fragments cited" and link to the
        full topic browser.
      - Level 2 fragment pages: used by the article template to render a
        "← Back to <topic>" affordance so readers can hop to the rest of
        the corpus on the same subject.
    """
    try:
        rel = filepath.relative_to(WIKI_DIR / "knowledge")
    except ValueError:
        return None
    parts = rel.parts
    if len(parts) < 2:
        return None
    slug = parts[0]
    topic_dir = WIKI_DIR / "knowledge" / slug
    if not topic_dir.is_dir():
        return None
    total = sum(
        1
        for f in topic_dir.rglob("*.md")
        if f.name not in ("_index.md", "index.md")
    )
    display_name = TOPIC_NAMES.get(slug, slug.replace("-", " ").title())
    return {
        "slug": slug,
        "display_name": display_name,
        "total_fragments": total,
    }


@app.route("/article/<path:article_path>")
def view_article(article_path):
    filepath = MERIDIAN_ROOT / article_path
    if not filepath.exists() or not str(filepath).startswith(str(MERIDIAN_ROOT)):
        return "Not found", 404

    article = read_article(filepath)

    # A synthesis page (path ending in knowledge/<slug>/index.md) should
    # have the citations section annotated with the full fragment count.
    # A fragment page (anything else under knowledge/<slug>/) uses the
    # same context to render a "Back to topic" banner.
    topic_context = _topic_context_for(filepath)
    is_synthesis = filepath.name == "index.md" and topic_context is not None

    body_html = render_markdown(
        article["body"],
        topic_context=topic_context if is_synthesis else None,
    )

    return render_template(
        "article.html",
        article=article,
        body_html=body_html,
        topic_context=topic_context,
        is_synthesis=is_synthesis,
    )


@app.route("/client/<slug>")
def view_client(slug):
    client_dir = WIKI_DIR / "clients" / "current" / slug
    if not client_dir.exists():
        client_dir = WIKI_DIR / "clients" / "former" / slug
    if not client_dir.exists():
        return "Client not found", 404

    # Read _index.md
    index_path = client_dir / "_index.md"
    index_article = read_article(index_path) if index_path.exists() else None
    index_html = render_markdown(index_article["body"]) if index_article else ""

    # List all articles
    articles = []
    for f in sorted(client_dir.glob("*.md")):
        if f.name == "_index.md":
            continue
        articles.append(read_article(f))

    return render_template("client.html", slug=slug,
                           index_article=index_article, index_html=index_html,
                           articles=articles)


def _fragment_preview(body: str, limit: int = 180) -> str:
    """Extract a short, plain-text preview from a fragment body.

    Strips markdown headers, frontmatter leftovers, and collapses
    whitespace so the topic page can show a skimmable snippet under
    each fragment link.
    """
    text = re.sub(r"^#+\s.*$", "", body, flags=re.MULTILINE)  # drop headers
    text = re.sub(r"\[\[([^\]]+)\]\]", "", text)              # drop wikilinks
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    truncated = text[:limit].rsplit(" ", 1)[0]
    return truncated + "…"


def _enrich_fragment(article: dict) -> dict:
    """Add derived fields for template rendering: client display name,
    preview snippet, sortable date. Mutates and returns the dict."""
    article["client_display"] = client_display_name(article.get("client_source", ""))
    article["preview"] = _fragment_preview(article.get("body", ""))
    # Unified sort key: prefer updated, fall back to created, fall back to empty
    article["sort_date"] = (
        article.get("updated") or article.get("created") or ""
    )
    return article


@app.route("/industry/<slug>")
def view_industry(slug):
    """Industries are the third knowledge dimension alongside topics and
    clients. Content rendering mirrors view_topic — Layer 3 synthesis
    at the top, Layer 2 fragment list below, searchable and filterable."""
    industry_dir = WIKI_DIR / "industries" / slug
    if not industry_dir.exists():
        return "Industry not found", 404

    # Prefer industries.yaml display name
    industries_yaml_path = MERIDIAN_ROOT / "industries.yaml"
    industry_name = slug.replace("-", " ").title()
    if industries_yaml_path.exists():
        try:
            data = yaml.safe_load(industries_yaml_path.read_text(encoding="utf-8")) or {}
            for entry in data.get("industries", []):
                if isinstance(entry, dict) and entry.get("slug") == slug:
                    industry_name = entry.get("name") or industry_name
                    break
        except yaml.YAMLError:
            pass

    # Layer 3 synthesis (if present)
    synthesis = None
    synthesis_html = ""
    index_file = industry_dir / "index.md"
    if index_file.exists():
        synthesis = read_article(index_file)
        if synthesis.get("frontmatter", {}).get("layer") == 3:
            industry_name = synthesis.get("title") or industry_name
            synthesis_html = render_markdown(synthesis["body"])
        else:
            synthesis = None

    # Layer 2 fragments
    articles = []
    for f in sorted(industry_dir.rglob("*.md")):
        if f.name in ("_index.md", "index.md", "PLACEHOLDER.md"):
            continue
        articles.append(_enrich_fragment(read_article(f)))
    articles.sort(key=lambda a: a.get("sort_date", ""), reverse=True)

    clients_on_topic = sorted(
        {a["client_display"] for a in articles if a.get("client_display")}
    )

    # Reuse the topic template — same shape, same rendering needs
    return render_template(
        "topic.html",
        slug=slug,
        topic_name=industry_name,
        articles=articles,
        synthesis=synthesis,
        synthesis_html=synthesis_html,
        clients_on_topic=clients_on_topic,
        dimension="industry",
    )


@app.route("/topic/<slug>")
def view_topic(slug):
    topic_dir = WIKI_DIR / "knowledge" / slug
    if not topic_dir.exists():
        return "Topic not found", 404

    # Prefer the topics.yaml display name, then the Layer 3 title, then
    # a title-cased slug as a last resort.
    topic_name = TOPIC_NAMES.get(slug, slug.replace("-", " ").title())

    # Check for Layer 3 synthesis
    synthesis = None
    synthesis_html = ""
    index_file = topic_dir / "index.md"
    if index_file.exists():
        synthesis = read_article(index_file)
        if synthesis.get("frontmatter", {}).get("layer") == 3:
            topic_name = synthesis.get("title", topic_name)
            topic_context = _topic_context_for(index_file)
            synthesis_html = render_markdown(
                synthesis["body"],
                topic_context=topic_context,
            )
        else:
            synthesis = None

    # List Layer 2 articles, enriched with preview + client display name.
    # Sort newest-first by default — most people want the recent stuff.
    articles = []
    for f in sorted(topic_dir.rglob("*.md")):
        if f.name in ("_index.md", "index.md"):
            continue
        articles.append(_enrich_fragment(read_article(f)))
    articles.sort(key=lambda a: a.get("sort_date", ""), reverse=True)

    # Distinct client set for the filter chips, sorted alphabetically.
    clients_on_topic = sorted(
        {a["client_display"] for a in articles if a.get("client_display")}
    )

    return render_template(
        "topic.html",
        slug=slug,
        topic_name=topic_name,
        articles=articles,
        synthesis=synthesis,
        synthesis_html=synthesis_html,
        clients_on_topic=clients_on_topic,
    )


@app.route("/engineering/")
def engineering_index():
    """Engineering topics browse page — companion to the /topic/ route family."""
    engineering_topics = _load_engineering_topics_with_counts()
    projects = _load_projects_with_counts()
    stats = get_stats()
    return render_template(
        "engineering_index.html",
        engineering_topics=engineering_topics,
        projects=projects,
        stats=stats,
    )


@app.route("/engineering/<slug>")
def view_engineering_topic(slug):
    """Render a single engineering topic page."""
    topic_dir = ENGINEERING_DIR / slug
    if not topic_dir.exists():
        return "Engineering topic not found", 404

    topic_name = ENGINEERING_TOPIC_NAMES.get(slug, slug.replace("-", " ").title())

    # Check for Layer 3 synthesis
    synthesis = None
    synthesis_html = ""
    index_file = topic_dir / "index.md"
    if index_file.exists():
        synthesis = read_article(index_file)
        if synthesis.get("frontmatter", {}).get("layer") == 3:
            topic_name = synthesis.get("title", topic_name)
            synthesis_html = render_markdown(synthesis["body"])
        else:
            synthesis = None

    # List Layer 2 fragments, enriched with project + commit metadata
    articles = []
    for f in sorted(topic_dir.rglob("*.md")):
        if f.name in ("_index.md", "index.md", "README.md"):
            continue
        articles.append(_enrich_engineering_fragment(read_article(f)))
    articles.sort(key=lambda a: a.get("sort_date", ""), reverse=True)

    # Distinct project set for the filter chips
    projects_on_topic = sorted(
        {a["project_display"] for a in articles if a.get("project_display")}
    )

    return render_template(
        "engineering_topic.html",
        slug=slug,
        topic_name=topic_name,
        articles=articles,
        synthesis=synthesis,
        synthesis_html=synthesis_html,
        projects_on_topic=projects_on_topic,
    )


@app.route("/project/<slug>")
def view_project(slug):
    """Render a single project page — its commits grouped by engineering topic."""
    project = next((p for p in PROJECTS if p.get("slug") == slug), None)
    if project is None:
        return "Project not found", 404

    # Gather commits for this project across both capture (unclassified)
    # and wiki/engineering/ (classified by topic).
    commits_by_topic: dict[str, list] = {}
    # Classified: walk wiki/engineering/<topic>/<slug>-*.md
    if ENGINEERING_DIR.exists():
        for f in ENGINEERING_DIR.rglob(f"{slug}-*.md"):
            if f.name in ("README.md", "_index.md", "index.md"):
                continue
            topic = f.parent.name
            commits_by_topic.setdefault(topic, []).append(_enrich_engineering_fragment(read_article(f)))
    # Unclassified: capture/external/commits/<slug>/
    unclassified = []
    capture_dir = COMMITS_CAPTURE_DIR / slug
    if capture_dir.exists():
        for f in sorted(capture_dir.glob("*.md")):
            unclassified.append(_enrich_engineering_fragment(read_article(f)))

    # Sort each topic bucket newest-first
    for topic in commits_by_topic:
        commits_by_topic[topic].sort(key=lambda a: a.get("sort_date", ""), reverse=True)
    unclassified.sort(key=lambda a: a.get("sort_date", ""), reverse=True)

    # Topic counts sorted descending for display
    topic_counts = sorted(
        [(topic, ENGINEERING_TOPIC_NAMES.get(topic, topic.replace("-", " ").title()), len(fragments))
         for topic, fragments in commits_by_topic.items()],
        key=lambda x: -x[2],
    )

    total_commits = sum(len(f) for f in commits_by_topic.values()) + len(unclassified)

    return render_template(
        "project.html",
        project=project,
        topic_counts=topic_counts,
        commits_by_topic=commits_by_topic,
        unclassified=unclassified,
        total_commits=total_commits,
    )


def _enrich_engineering_fragment(article: dict) -> dict:
    """Enrich a commit fragment with display-friendly project + date fields."""
    fm = article.get("frontmatter", {}) or {}
    project_slug = fm.get("source_project", "")
    project_display = project_slug  # projects.yaml display names are loaded elsewhere; slug is fine here
    for p in PROJECTS:
        if p.get("slug") == project_slug:
            project_display = p.get("name", project_slug)
            break
    article["project_slug"] = project_slug
    article["project_display"] = project_display
    article["commit_short_sha"] = fm.get("commit_short_sha", "")
    article["files_changed"] = fm.get("files_changed", 0)
    article["insertions"] = fm.get("insertions", 0)
    article["deletions"] = fm.get("deletions", 0)
    article["sort_date"] = fm.get("source_date", "")
    article["topic_slug"] = fm.get("topic_slug", "unclassified")
    article["classification_confidence"] = fm.get("classification_confidence", "")
    # Use the commit subject as title; fall back to whatever read_article gave us
    article["title"] = fm.get("title") or article.get("title", "")
    # Preview from body
    body = article.get("body", "")
    preview = body[:300].replace("\n", " ").strip()
    article["preview"] = preview
    article["word_count"] = len(body.split())
    # Path for link construction
    path = article.get("path", "")
    if isinstance(path, Path):
        try:
            article["path"] = path.relative_to(MERIDIAN_ROOT).as_posix()
        except ValueError:
            article["path"] = str(path)
    return article


@app.route("/analytics/")
def analytics_page():
    """Deep insights into the data — provenance, density, freshness, gaps."""
    analytics = _compute_analytics()
    return render_template("analytics.html", analytics=analytics)


def _coerce_date_str(value) -> str:
    """Normalize a frontmatter date field to a YYYY-MM-DD string.

    PyYAML auto-parses ISO-like date strings into datetime.date objects,
    which then fail `isinstance(x, str)` checks downstream. Handle both.
    """
    if not value:
        return ""
    if isinstance(value, str):
        return value[:10] if len(value) >= 10 else value
    # datetime.date / datetime.datetime
    try:
        return value.strftime("%Y-%m-%d")
    except Exception:
        return str(value)[:10]


def _read_frontmatter_only(path: Path) -> dict:
    """Fast read: parse only the YAML frontmatter and return it as a dict.
    Returns empty dict if no frontmatter or parse error."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    try:
        end = text.index("\n---\n", 4)
    except ValueError:
        return {}
    try:
        fm = yaml.safe_load(text[4:end]) or {}
    except yaml.YAMLError:
        return {}
    return fm if isinstance(fm, dict) else {}


def _compute_analytics() -> dict:
    """Walk wiki/ and capture/ and aggregate analytics.

    Returns a dict with four sections: provenance, coverage, freshness, gaps.
    Currently recomputes on every request. For 4500 files this is fast
    enough (~1s). Add caching if it becomes a bottleneck.
    """
    # -----------------------------------------------------------------
    # Gather every fragment we care about with its namespace + frontmatter
    # -----------------------------------------------------------------
    fragments: list[dict] = []

    namespace_dirs = [
        ("knowledge",   WIKI_DIR / "knowledge"),
        ("industries",  WIKI_DIR / "industries"),
        ("clients",     WIKI_DIR / "clients"),
        ("engineering", WIKI_DIR / "engineering"),
    ]
    for ns, d in namespace_dirs:
        if not d.exists():
            continue
        for f in d.rglob("*.md"):
            if f.name in ("_index.md", "README.md", "PLACEHOLDER.md"):
                continue
            fm = _read_frontmatter_only(f)
            layer = fm.get("layer", 2)
            fragments.append({
                "path": f.relative_to(MERIDIAN_ROOT).as_posix(),
                "namespace": ns,
                "is_synthesis": f.name == "index.md" and layer == 3,
                "layer": layer,
                "source_type": fm.get("source_type", ""),
                "source_origin": fm.get("source_origin", ""),
                "source_date": _coerce_date_str(fm.get("source_date", "")),
                "source_project": fm.get("source_project", ""),
                "topic_slug": fm.get("topic_slug", ""),
                "classification_confidence": fm.get("classification_confidence", ""),
            })

    # Unclassified capture
    if COMMITS_CAPTURE_DIR.exists():
        for f in COMMITS_CAPTURE_DIR.rglob("*.md"):
            fm = _read_frontmatter_only(f)
            fragments.append({
                "path": f.relative_to(MERIDIAN_ROOT).as_posix(),
                "namespace": "unclassified",
                "is_synthesis": False,
                "layer": 2,
                "source_type": fm.get("source_type", "internal-commit"),
                "source_origin": fm.get("source_origin", "git"),
                "source_date": _coerce_date_str(fm.get("source_date", "")),
                "source_project": fm.get("source_project", ""),
                "topic_slug": "unclassified",
                "classification_confidence": "",
            })

    total_fragments = len(fragments)

    # -----------------------------------------------------------------
    # A. Source Provenance
    # -----------------------------------------------------------------
    by_namespace: dict[str, int] = defaultdict(int)
    by_source_type: dict[str, int] = defaultdict(int)
    by_source_origin: dict[str, int] = defaultdict(int)
    for frag in fragments:
        by_namespace[frag["namespace"]] += 1
        st = frag["source_type"] or "(unspecified)"
        by_source_type[st] += 1
        so = frag["source_origin"] or "(unspecified)"
        by_source_origin[so] += 1

    # Time series: fragments per month (last 24 months that have data)
    per_month: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for frag in fragments:
        d = frag["source_date"]
        if isinstance(d, str) and len(d) >= 7:
            month = d[:7]  # YYYY-MM
            per_month[month][frag["namespace"]] += 1
    # Sort + take last 24 months with any data
    months_sorted = sorted(per_month.keys())[-24:]
    time_series = {
        "months": months_sorted,
        "by_namespace": {
            ns: [per_month[m].get(ns, 0) for m in months_sorted]
            for ns in ("knowledge", "engineering", "industries", "clients", "unclassified")
        },
    }

    provenance = {
        "total": total_fragments,
        "by_namespace": dict(sorted(by_namespace.items(), key=lambda x: -x[1])),
        "by_source_type": dict(sorted(by_source_type.items(), key=lambda x: -x[1])),
        "by_source_origin": dict(sorted(by_source_origin.items(), key=lambda x: -x[1])),
        "time_series": time_series,
    }

    # -----------------------------------------------------------------
    # B. Coverage & Density
    # -----------------------------------------------------------------
    topic_counts: dict[tuple[str, str], int] = defaultdict(int)
    for frag in fragments:
        if frag["is_synthesis"]:
            continue
        parts = frag["path"].split("/")
        if len(parts) >= 3 and parts[0] == "wiki":
            ns = parts[1]
            topic_slug = parts[2]
            topic_counts[(ns, topic_slug)] += 1

    # Histogram buckets
    buckets = {"0": 0, "1-4": 0, "5-19": 0, "20-49": 0, "50-99": 0, "100+": 0}
    for count in topic_counts.values():
        if count == 0:
            buckets["0"] += 1
        elif count < 5:
            buckets["1-4"] += 1
        elif count < 20:
            buckets["5-19"] += 1
        elif count < 50:
            buckets["20-49"] += 1
        elif count < 100:
            buckets["50-99"] += 1
        else:
            buckets["100+"] += 1

    top_topics = sorted(topic_counts.items(), key=lambda x: -x[1])[:10]
    bottom_topics = [
        (k, v) for k, v in sorted(topic_counts.items(), key=lambda x: x[1])[:10] if v > 0
    ]

    coverage = {
        "topic_count": len(topic_counts),
        "histogram": buckets,
        "top_topics": [
            {"namespace": ns, "slug": slug, "count": count}
            for (ns, slug), count in top_topics
        ],
        "bottom_topics": [
            {"namespace": ns, "slug": slug, "count": count}
            for (ns, slug), count in bottom_topics
        ],
        "unclassified_count": by_namespace.get("unclassified", 0),
    }

    # -----------------------------------------------------------------
    # C. Freshness — calendar heatmap data + stalest topics
    # -----------------------------------------------------------------
    # Last 180 days of activity, one bucket per day
    from datetime import timedelta
    today = datetime.now().date()
    earliest = today - timedelta(days=180)
    per_day: dict[str, int] = defaultdict(int)
    for frag in fragments:
        d = frag["source_date"]
        if isinstance(d, str) and len(d) >= 10:
            try:
                frag_date = datetime.strptime(d[:10], "%Y-%m-%d").date()
            except ValueError:
                continue
            if frag_date >= earliest:
                per_day[d[:10]] += 1

    # Build ordered day list for the heatmap
    heatmap_days = []
    cur = earliest
    while cur <= today:
        heatmap_days.append({
            "date": cur.strftime("%Y-%m-%d"),
            "count": per_day.get(cur.strftime("%Y-%m-%d"), 0),
            "weekday": cur.weekday(),  # 0=Mon
        })
        cur = cur + timedelta(days=1)

    # Stalest topics: latest source_date per topic, sorted ascending (oldest first)
    topic_latest_date: dict[tuple[str, str], str] = {}
    for frag in fragments:
        if frag["is_synthesis"]:
            continue
        parts = frag["path"].split("/")
        if len(parts) >= 3 and parts[0] == "wiki":
            ns, slug = parts[1], parts[2]
            d = frag["source_date"]
            if isinstance(d, str) and len(d) >= 10:
                existing = topic_latest_date.get((ns, slug), "")
                if d > existing:
                    topic_latest_date[(ns, slug)] = d[:10]
    stalest = sorted(
        [(k, v) for k, v in topic_latest_date.items() if v],
        key=lambda x: x[1],
    )[:10]
    stalest_list = []
    for (ns, slug), last in stalest:
        try:
            last_dt = datetime.strptime(last, "%Y-%m-%d").date()
            days_since = (today - last_dt).days
        except ValueError:
            days_since = 0
        stalest_list.append({
            "namespace": ns,
            "slug": slug,
            "last_date": last,
            "days_since": days_since,
        })

    freshness = {
        "heatmap": heatmap_days,
        "heatmap_max": max((d["count"] for d in heatmap_days), default=1),
        "stalest_topics": stalest_list,
    }

    # -----------------------------------------------------------------
    # D. Gaps & Opportunities
    # -----------------------------------------------------------------
    # Topics with 0 external evidence (all currently — placeholder until Readwise)
    external_source_types = {"external-blog", "external-academic", "external-vendor",
                              "external-case-study", "external-whitepaper", "external-book",
                              "external-platform-docs"}
    topics_with_external: set[tuple[str, str]] = set()
    topics_with_internal: set[tuple[str, str]] = set()
    for frag in fragments:
        if frag["is_synthesis"]:
            continue
        parts = frag["path"].split("/")
        if len(parts) >= 3 and parts[0] == "wiki":
            ns, slug = parts[1], parts[2]
            if frag["source_type"] in external_source_types:
                topics_with_external.add((ns, slug))
            else:
                topics_with_internal.add((ns, slug))

    all_topic_keys = set(topic_counts.keys())
    topics_internal_only = all_topic_keys - topics_with_external
    topics_external_only = all_topic_keys - topics_with_internal
    topics_both = topics_with_internal & topics_with_external

    sparse_topics = sorted(
        [(k, v) for k, v in topic_counts.items() if 0 < v < 10],
        key=lambda x: x[1],
    )

    gaps = {
        "topics_internal_only": len(topics_internal_only),
        "topics_external_only": len(topics_external_only),
        "topics_both": len(topics_both),
        "sparse_topics": [
            {"namespace": ns, "slug": slug, "count": count}
            for (ns, slug), count in sparse_topics
        ],
        "unclassified_commits": by_namespace.get("unclassified", 0),
    }

    return {
        "total_fragments": total_fragments,
        "provenance": provenance,
        "coverage": coverage,
        "freshness": freshness,
        "gaps": gaps,
    }


@app.route("/graph/")
def graph_page():
    """Knowledge graph visualization — Cytoscape.js force-directed network."""
    return render_template("graph.html")


@app.route("/graph/data.json")
def graph_data():
    """Return the knowledge graph as Cytoscape.js nodes/edges JSON."""
    return jsonify(_compute_knowledge_graph())


def _compute_knowledge_graph() -> dict:
    """Walk the wiki and build a container-level knowledge graph.

    Nodes: topics (business/engineering), industries, clients, projects.
    Edges: topic→topic (via Related Topics wikilinks in L3 articles),
    client→topic (citations in client stubs), project→engineering-topic,
    industry→client (from clients.yaml), industry→business-topic (via
    wiki/industries content).
    """
    nodes: list[dict] = []
    edges: list[dict] = []
    seen_edges: set[tuple[str, str]] = set()

    def add_edge(source: str, target: str, kind: str) -> None:
        if source == target:
            return
        key = (source, target) if source < target else (target, source)
        if key in seen_edges:
            return
        seen_edges.add(key)
        edges.append({
            "data": {
                "id": f"{source}__{target}",
                "source": source,
                "target": target,
                "kind": kind,
            }
        })

    # ---- Business topics from wiki/knowledge ----
    knowledge_dir = WIKI_DIR / "knowledge"
    business_topic_slugs: set[str] = set()
    if knowledge_dir.exists():
        for d in knowledge_dir.iterdir():
            if not d.is_dir():
                continue
            slug = d.name
            frag_count = sum(
                1 for f in d.rglob("*.md")
                if f.name not in ("_index.md", "index.md")
            )
            has_l3 = False
            if (d / "index.md").exists():
                try:
                    if "layer: 3" in (d / "index.md").read_text(encoding="utf-8", errors="replace"):
                        has_l3 = True
                except Exception:
                    pass
            node_id = f"topic:{slug}"
            business_topic_slugs.add(slug)
            nodes.append({
                "data": {
                    "id": node_id,
                    "label": TOPIC_NAMES.get(slug, slug.replace("-", " ").title()),
                    "type": "business_topic",
                    "namespace": "business",
                    "size": max(6, min(40, frag_count // 3 + 6)),
                    "fragment_count": frag_count,
                    "has_l3": has_l3,
                    "href": f"/topic/{slug}",
                }
            })

    # ---- Engineering topics ----
    eng_topic_slugs: set[str] = set()
    if ENGINEERING_DIR.exists():
        for d in ENGINEERING_DIR.iterdir():
            if not d.is_dir():
                continue
            slug = d.name
            frag_count = sum(
                1 for f in d.glob("*.md")
                if f.name not in ("README.md", "_index.md", "index.md")
            )
            has_l3 = (d / "index.md").exists()
            node_id = f"eng:{slug}"
            eng_topic_slugs.add(slug)
            nodes.append({
                "data": {
                    "id": node_id,
                    "label": ENGINEERING_TOPIC_NAMES.get(slug, slug.replace("-", " ").title()),
                    "type": "engineering_topic",
                    "namespace": "engineering",
                    "size": max(6, min(40, frag_count // 10 + 6)),
                    "fragment_count": frag_count,
                    "has_l3": has_l3,
                    "href": f"/engineering/{slug}",
                }
            })

    # ---- Industries ----
    industries_dir = WIKI_DIR / "industries"
    industry_slugs: set[str] = set()
    if industries_dir.exists():
        for d in industries_dir.iterdir():
            if not d.is_dir():
                continue
            slug = d.name
            frag_count = sum(
                1 for f in d.glob("*.md")
                if f.name not in ("_index.md", "index.md", "PLACEHOLDER.md")
            )
            node_id = f"industry:{slug}"
            industry_slugs.add(slug)
            nodes.append({
                "data": {
                    "id": node_id,
                    "label": slug.replace("-", " ").title(),
                    "type": "industry",
                    "namespace": "industry",
                    "size": max(6, min(40, frag_count // 2 + 8)),
                    "fragment_count": frag_count,
                    "href": f"/industry/{slug}",
                }
            })

    # ---- Clients ----
    client_slugs: set[str] = set()
    clients_dir = WIKI_DIR / "clients" / "current"
    if clients_dir.exists():
        for d in clients_dir.iterdir():
            if not d.is_dir():
                continue
            slug = d.name
            node_id = f"client:{slug}"
            client_slugs.add(slug)
            nodes.append({
                "data": {
                    "id": node_id,
                    "label": CLIENT_NAMES.get(slug, slug.replace("-", " ").title()),
                    "type": "client",
                    "namespace": "client",
                    "size": 10,
                    "href": f"/client/{slug}",
                }
            })

    # ---- Projects ----
    for p in PROJECTS:
        slug = p.get("slug", "")
        if not slug:
            continue
        commit_count = 0
        if ENGINEERING_DIR.exists():
            commit_count = sum(
                1
                for f in ENGINEERING_DIR.rglob(f"{slug}-*.md")
                if f.name not in ("README.md", "_index.md", "index.md")
            )
        node_id = f"project:{slug}"
        nodes.append({
            "data": {
                "id": node_id,
                "label": p.get("name", slug),
                "type": "project",
                "namespace": "project",
                "size": max(6, min(40, commit_count // 8 + 6)),
                "fragment_count": commit_count,
                "href": f"/project/{slug}",
            }
        })

    # ---- Edges: Topic → Topic (from Related Topics in L3 articles) ----
    wikilink_pat = re.compile(r"\[\[([^|\]]+)")
    if knowledge_dir.exists():
        for idx in knowledge_dir.rglob("index.md"):
            try:
                content = idx.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            if "layer: 3" not in content:
                continue
            source_slug = idx.parent.name
            source_id = f"topic:{source_slug}"
            # Find a "## Related Topics" section and extract wikilinks
            related_match = re.search(r"##\s+Related Topics\s*\n(.*?)(?=\n##|\Z)", content, re.DOTALL)
            if not related_match:
                continue
            section = related_match.group(1)
            for link in wikilink_pat.findall(section):
                # Extract slug from link like "wiki/knowledge/seo" or "seo"
                target_slug = link.strip().split("/")[-1].replace(".md", "")
                if target_slug in business_topic_slugs and target_slug != source_slug:
                    add_edge(source_id, f"topic:{target_slug}", "related")

    # ---- Edges: Client → Business topic (from client _index.md wikilinks) ----
    if clients_dir.exists():
        for d in clients_dir.iterdir():
            if not d.is_dir():
                continue
            client_slug = d.name
            idx = d / "_index.md"
            if not idx.exists():
                continue
            try:
                content = idx.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            # Match links to knowledge/ topics
            for m in re.finditer(r"\[\[knowledge/([^|\]/]+)", content):
                topic = m.group(1).replace(".md", "")
                if topic in business_topic_slugs:
                    add_edge(f"client:{client_slug}", f"topic:{topic}", "cited_by")

    # ---- Edges: Project → Engineering topic (from classified commits) ----
    if ENGINEERING_DIR.exists():
        project_topic_edges: set[tuple[str, str]] = set()
        for f in ENGINEERING_DIR.rglob("*.md"):
            if f.name in ("README.md", "_index.md", "index.md"):
                continue
            topic_slug = f.parent.name
            # Filename format: <project-slug>-<short-sha>-<slug>.md
            name = f.stem
            # Project slug is everything up to the first hex-looking segment
            for p in PROJECTS:
                ps = p.get("slug", "")
                if ps and name.startswith(f"{ps}-"):
                    project_topic_edges.add((ps, topic_slug))
                    break
        for proj, topic in project_topic_edges:
            if topic in eng_topic_slugs:
                add_edge(f"project:{proj}", f"eng:{topic}", "has_commits")

    # ---- Edges: Industry → Client (from clients.yaml industry field) ----
    if CLIENTS_YAML.exists():
        try:
            data = yaml.safe_load(CLIENTS_YAML.read_text(encoding="utf-8")) or {}
            for entry in data.get("clients", []):
                if not isinstance(entry, dict):
                    continue
                client_slug = entry.get("slug", "")
                industry = entry.get("industry", "")
                if client_slug and industry and client_slug in client_slugs and industry in industry_slugs:
                    add_edge(f"industry:{industry}", f"client:{client_slug}", "contains")
        except yaml.YAMLError:
            pass

    return {"nodes": nodes, "edges": edges, "stats": {
        "node_count": len(nodes),
        "edge_count": len(edges),
    }}


@app.route("/ask", methods=["GET", "POST"])
def ask_page():
    if request.method == "GET":
        return render_template("ask.html", question="", answer="")

    question = request.form.get("question", "")
    if not question:
        return render_template("ask.html", question="", answer="")

    try:
        resp = requests.post(
            f"{RECEIVER_URL}/ask",
            headers=receiver_headers(),
            json={"question": question},
            timeout=120,
        )
        result = resp.json()
        raw_answer = result.get("result", result.get("error", "No answer"))
        answer_html = render_markdown(raw_answer)
    except Exception as e:
        answer_html = f"<p>Error: {e}</p>"

    return render_template("ask.html", question=question, answer_html=answer_html)


def make_download_name(filepath: Path, article_path: str) -> str:
    """Build a human-readable download filename from the article path."""
    parts = Path(article_path).parts  # e.g. wiki/knowledge/google-ads/index.md
    if filepath.name == "index.md":
        # Synthesis article: use topic/client name
        parent = filepath.parent.name  # e.g. "google-ads"
        return f"{parent}-synthesis.md"
    if filepath.name == "_index.md":
        parent = filepath.parent.name
        return f"{parent}-overview.md"
    return filepath.name


@app.route("/download/md/<path:article_path>")
def download_md(article_path):
    """Download article as raw markdown."""
    filepath = MERIDIAN_ROOT / article_path
    if not filepath.exists() or not str(filepath).startswith(str(MERIDIAN_ROOT)):
        return "Not found", 404
    name = make_download_name(filepath, article_path)
    return send_file(filepath, as_attachment=True, download_name=name,
                     mimetype="text/markdown")


@app.route("/download/pdf/<path:article_path>")
def download_pdf(article_path):
    """Download article as PDF."""
    filepath = MERIDIAN_ROOT / article_path
    if not filepath.exists() or not str(filepath).startswith(str(MERIDIAN_ROOT)):
        return "Not found", 404

    article = read_article(filepath)
    body_html = render_markdown(article["body"])

    html = f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<title>{article['title']}</title>
<style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif; max-width: 800px; margin: 40px auto; padding: 0 20px; line-height: 1.7; color: #1f2937; }}
    h1 {{ font-size: 1.8rem; margin: 1.5rem 0 0.75rem; }}
    h2 {{ font-size: 1.4rem; margin: 1.25rem 0 0.5rem; border-bottom: 1px solid #e5e7eb; padding-bottom: 0.25rem; }}
    h3 {{ font-size: 1.1rem; margin: 1rem 0 0.5rem; }}
    p {{ margin: 0.75rem 0; }}
    ul, ol {{ margin: 0.75rem 0; padding-left: 1.5rem; }}
    li {{ margin: 0.25rem 0; }}
    code {{ background: #f3f4f6; padding: 0.1rem 0.3rem; border-radius: 3px; font-size: 0.9em; }}
    pre {{ background: #f3f4f6; padding: 1rem; border-radius: 6px; overflow-x: auto; }}
    blockquote {{ border-left: 3px solid #d1d5db; padding-left: 1rem; color: #6b7280; margin: 1rem 0; }}
    table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; }}
    th, td {{ border: 1px solid #e5e7eb; padding: 0.5rem 0.75rem; text-align: left; }}
    th {{ background: #f3f4f6; font-weight: 600; }}
    a {{ color: #2563eb; }}
    .meta {{ font-size: 0.8rem; color: #6b7280; margin-bottom: 1rem; }}
</style>
</head><body>
<div class="meta">{article['path']} · {article.get('word_count', '')} words · {article.get('updated', '')}</div>
{body_html}
</body></html>"""

    try:
        from weasyprint import HTML
        import io
        pdf_bytes = HTML(string=html).write_pdf()
        pdf_name = make_download_name(filepath, article_path).replace(".md", ".pdf")
        return Response(
            pdf_bytes,
            mimetype="application/pdf",
            headers={"Content-Disposition": f"attachment; filename={pdf_name}"},
        )
    except Exception as e:
        # Fallback: return styled HTML
        html_name = make_download_name(filepath, article_path).replace(".md", ".html")
        return Response(html, mimetype="text/html",
                        headers={"Content-Disposition": f"attachment; filename={html_name}"})


@app.route("/api/stats")
def api_stats():
    return jsonify(get_stats())


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("WEB_PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=True)
