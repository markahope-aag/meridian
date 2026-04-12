#!/usr/bin/env python3
"""Meridian Web UI — dashboard and search interface for the wiki.

Replaces Obsidian as the primary way to browse and interact with Meridian.
Reads directly from /meridian/ filesystem. Calls receiver API for agent actions.
"""

import hmac
import json
import os
import re
import secrets
from collections import defaultdict
from datetime import datetime
from functools import wraps
from pathlib import Path

from flask import (
    Flask, Response, abort, g, jsonify, redirect,
    render_template, request, send_file, session, url_for,
)
import requests
import yaml

# Critical helpers imported from web.helpers, web.registry, web.config.
# These modules are the canonical, tested versions. The vm-auto-deploy
# script copies web/*.py into /app/web/ inside the container so imports
# resolve at runtime.
from web.config import (
    MERIDIAN_ROOT, WIKI_DIR, RAW_DIR, CAPTURE_DIR, REPORTS_DIR,
    ENGINEERING_DIR, INTERESTS_DIR, LAYER4_DIR,
    COMMITS_CAPTURE_DIR, INTERESTS_CAPTURE_DIR,
    CLIENTS_YAML, TOPICS_YAML, ENGINEERING_TOPICS_YAML,
    PROJECTS_YAML, INTERESTS_TOPICS_YAML,
    RECEIVER_URL, RECEIVER_TOKEN,
)
from web.helpers import (
    parse_frontmatter, read_article, render_markdown,
    sanitize_html, safe_resolve as _safe_resolve,
    coerce_date_str as _coerce_date_str,
)
from web.registry import (
    CLIENT_NAMES, TOPIC_NAMES, ENGINEERING_TOPIC_NAMES,
    INTERESTS_TOPIC_NAMES, PROJECTS,
    client_display_name, _non_synthesizable_topic_slugs,
)


app = Flask(__name__)
app.secret_key = os.environ.get(
    "MERIDIAN_SECRET_KEY",
    secrets.token_hex(32),  # fallback: random per-restart (sessions won't survive restarts)
)

# ---------------------------------------------------------------------------
# Authentication — session-based login
# ---------------------------------------------------------------------------
# Set MERIDIAN_DASHBOARD_PASSWORD as a Coolify env var. If unset, auth is
# disabled (dev mode). Username is always "admin".

DASHBOARD_PASSWORD = os.environ.get("MERIDIAN_DASHBOARD_PASSWORD", "")


def require_login(f):
    """Decorator that redirects unauthenticated users to /login."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not DASHBOARD_PASSWORD:
            # Auth disabled (no password configured) — open access
            return f(*args, **kwargs)
        if not session.get("authenticated"):
            return redirect(url_for("login_page", next=request.path))
        return f(*args, **kwargs)
    return decorated


@app.route("/login", methods=["GET", "POST"])
def login_page():
    """Simple login form. Password checked against MERIDIAN_DASHBOARD_PASSWORD env var."""
    error = ""
    if request.method == "POST":
        password = request.form.get("password", "")
        if hmac.compare_digest(password, DASHBOARD_PASSWORD):
            session["authenticated"] = True
            session.permanent = True
            next_url = request.args.get("next") or request.form.get("next") or "/"
            return redirect(next_url)
        error = "Incorrect password"
    return render_template("login.html", error=error, next=request.args.get("next", "/"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


# ---------------------------------------------------------------------------
# CSRF protection — manual token-per-session
# ---------------------------------------------------------------------------

def _generate_csrf_token() -> str:
    """Return the CSRF token for the current session, creating one if needed."""
    if "_csrf_token" not in session:
        session["_csrf_token"] = secrets.token_hex(32)
    return session["_csrf_token"]


def _validate_csrf():
    """Check that the submitted CSRF token matches the session token.
    Call this at the top of any POST handler that mutates state."""
    token = request.form.get("_csrf_token") or request.headers.get("X-CSRF-Token")
    if not token or not hmac.compare_digest(token, session.get("_csrf_token", "")):
        abort(403, description="CSRF token missing or invalid")


@app.before_request
def _inject_csrf_and_auth():
    """Make csrf_token available to all templates, and enforce auth on
    all routes except login/logout and static files."""
    g.csrf_token = _generate_csrf_token()

    # Auth enforcement — skip for login/logout, static, and health checks
    if DASHBOARD_PASSWORD:
        exempt = {"/login", "/logout", "/static", "/favicon.ico"}
        if request.path not in exempt and not request.path.startswith("/static"):
            if not session.get("authenticated"):
                if request.is_json:
                    abort(401, description="Authentication required")
                return redirect(url_for("login_page", next=request.path))


@app.context_processor
def _inject_template_globals():
    """Make csrf_token and auth state available in every template."""
    return {
        "csrf_token": _generate_csrf_token(),
        "is_authenticated": session.get("authenticated", False),
        "auth_enabled": bool(DASHBOARD_PASSWORD),
    }


MERIDIAN_ROOT = Path(os.environ.get("MERIDIAN_ROOT", "/meridian"))
WIKI_DIR = MERIDIAN_ROOT / "wiki"
RAW_DIR = MERIDIAN_ROOT / "raw"
CAPTURE_DIR = MERIDIAN_ROOT / "capture"
CLIENTS_YAML = MERIDIAN_ROOT / "clients.yaml"
TOPICS_YAML = MERIDIAN_ROOT / "topics.yaml"
REPORTS_DIR = MERIDIAN_ROOT / "reports"
ENGINEERING_TOPICS_YAML = MERIDIAN_ROOT / "engineering-topics.yaml"
PROJECTS_YAML = MERIDIAN_ROOT / "projects.yaml"
INTERESTS_TOPICS_YAML = MERIDIAN_ROOT / "interests-topics.yaml"
ENGINEERING_DIR = WIKI_DIR / "engineering"
INTERESTS_DIR = WIKI_DIR / "interests"
LAYER4_DIR = WIKI_DIR / "layer4"
COMMITS_CAPTURE_DIR = CAPTURE_DIR / "external" / "commits"
INTERESTS_CAPTURE_DIR = CAPTURE_DIR / "external" / "interests"
RECEIVER_URL = os.environ.get("MERIDIAN_RECEIVER_URL", "http://localhost:8000")
RECEIVER_TOKEN = os.environ.get("MERIDIAN_RECEIVER_TOKEN", "")


# ---------------------------------------------------------------------------
# Registry lookups — loaded once at startup, refreshed on demand via reload.
# ---------------------------------------------------------------------------


def receiver_headers():
    return {"Authorization": f"Bearer {RECEIVER_TOKEN}", "Content-Type": "application/json"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
        "wiki_total": 0,           # business domain only (excludes engineering + interests)
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
        # === Business namespace (symmetric metrics matching engineering + interests) ===
        "business_topics_registered": 0,
        "business_topics_with_fragments": 0,
        "business_fragments": 0,
        "business_l3": 0,
        "business_capture_queue": 0,
        # === Engineering namespace ===
        "engineering_topics_registered": 0,
        "engineering_topics_with_fragments": 0,
        "engineering_fragments": 0,
        "engineering_l3": 0,
        "projects_registered": 0,
        "projects_active": 0,
        "commits_ingested_total": 0,
        "capture_commits_unclassified": 0,  # classifier examined, no fit
        # === Interests namespace ===
        "interests_topics_registered": 0,
        "interests_topics_with_fragments": 0,
        "interests_fragments": 0,
        "interests_l3": 0,
        "interests_sources": 0,     # books / articles / reflections ingested
        "interests_capture_queue": 0,
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
            knowledge_topic_dirs = [d for d in knowledge_dir.iterdir() if d.is_dir()]
            stats["knowledge_topics"] = len(knowledge_topic_dirs)
            # Business namespace metrics matching the engineering/interests pattern
            stats["business_topics_with_fragments"] = sum(
                1 for d in knowledge_topic_dirs
                if any(
                    f.name not in ("_index.md", "index.md")
                    for f in d.rglob("*.md")
                )
            )
            # Fragments = everything except the index.md (L3 synthesis) and _index.md
            stats["business_fragments"] = sum(
                1 for f in knowledge_dir.rglob("*.md")
                if f.name not in ("_index.md", "index.md")
            )
            # L3 = count of <topic>/index.md files that actually contain layer: 3
            l3_count = 0
            for d in knowledge_topic_dirs:
                idx = d / "index.md"
                if idx.exists():
                    try:
                        if "layer: 3" in idx.read_text(encoding="utf-8", errors="replace"):
                            l3_count += 1
                    except Exception:
                        pass
            stats["business_l3"] = l3_count
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
        # Interests namespace
        if INTERESTS_DIR.exists():
            int_fragments = [
                f for f in INTERESTS_DIR.rglob("*.md")
                if f.name not in ("README.md", "_index.md")
            ]
            stats["interests_fragments"] = sum(
                1 for f in int_fragments if f.name != "index.md"
            )
            stats["interests_l3"] = sum(
                1 for f in int_fragments if f.name == "index.md"
            )
            int_topic_dirs = [d for d in INTERESTS_DIR.iterdir() if d.is_dir()]
            stats["interests_topics_with_fragments"] = sum(
                1 for d in int_topic_dirs
                if any(f.name not in ("README.md", "_index.md", "index.md")
                       for f in d.glob("*.md"))
            )
    # Engineering + project registry counts (drive from registry files,
    # not filesystem, so empty topics still show as registered)
    stats["business_topics_registered"] = len(TOPIC_NAMES) or stats.get("knowledge_topics", 0)
    stats["engineering_topics_registered"] = len(ENGINEERING_TOPIC_NAMES)
    stats["projects_registered"] = len(PROJECTS)
    stats["projects_active"] = sum(
        1 for p in PROJECTS if p.get("status") == "active"
    )
    stats["interests_topics_registered"] = len(INTERESTS_TOPIC_NAMES)

    if RAW_DIR.exists():
        stats["raw"] = sum(1 for _ in RAW_DIR.glob("*.md") if _.name != "_index.md")
    if CAPTURE_DIR.exists():
        stats["capture"] = sum(1 for _ in CAPTURE_DIR.glob("*.md"))
        stats["capture_total"] = sum(1 for _ in CAPTURE_DIR.rglob("*.md"))
    if COMMITS_CAPTURE_DIR.exists():
        # Two distinct buckets live in capture/external/commits/:
        #   1. Queue — fragments the classifier hasn't seen yet. Identified
        #      by the ABSENCE of `classification_confidence` in frontmatter.
        #      These are "actionable" — they need a classifier pass.
        #   2. Terminal unclassified — fragments the classifier examined
        #      and decided didn't fit any topic in the registry. Identified
        #      by PRESENCE of `classification_confidence`. These sit here
        #      until the topic registry grows enough to absorb them.
        queue = 0
        unclassified_terminal = 0
        for f in COMMITS_CAPTURE_DIR.rglob("*.md"):
            fm = _read_frontmatter_only(f)
            if fm.get("classification_confidence"):
                unclassified_terminal += 1
            else:
                queue += 1
        stats["capture_commits_queue"] = queue
        stats["capture_commits_unclassified"] = unclassified_terminal
        # Total commits ingested = all in capture/ + all classified (moved to wiki/engineering/)
        stats["commits_ingested_total"] = (
            queue + unclassified_terminal + stats["engineering_fragments"]
        )
    if INTERESTS_CAPTURE_DIR.exists():
        stats["interests_capture_queue"] = sum(
            1 for _ in INTERESTS_CAPTURE_DIR.rglob("*.md")
        )
    # Business capture queue = the top-level manual capture minus commits + interests
    stats["business_capture_queue"] = stats.get("capture", 0)
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
    # Interests topics — list from registry, enriched with fragment counts
    interests_topics = _load_interests_topics_with_counts()
    # Layer 4 conceptual summary
    layer4_articles = _load_layer4_articles()
    layer4_summary = _layer4_summary_from(layer4_articles)

    return render_template("dashboard.html",
                           stats=stats, recent_log=recent_log,
                           clients=clients, topics=topics, industries=industries,
                           engineering_topics=engineering_topics,
                           projects=projects,
                           interests_topics=interests_topics,
                           layer4_summary=layer4_summary,
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


def _load_interests_topics_with_counts() -> list[dict]:
    """Return interests topics from registry, enriched with fragment counts."""
    result = []
    if not INTERESTS_TOPICS_YAML.exists():
        return result
    try:
        data = yaml.safe_load(INTERESTS_TOPICS_YAML.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return result
    for entry in data.get("topics", []):
        if not isinstance(entry, dict):
            continue
        slug = entry.get("slug", "")
        if not slug:
            continue
        topic_dir = INTERESTS_DIR / slug
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
            "description": entry.get("description", ""),
            "fragment_count": fragment_count,
            "layer3": has_l3,
        })
    # Sort: L3 first, then by fragment count descending, then by name
    result.sort(key=lambda x: (not x["layer3"], -x["fragment_count"], x["name"]))
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


def _collect_unclassified_for_review() -> dict:
    """Walk wiki/engineering/unclassified/ and return pending fragments
    grouped by source project. Excludes fragments already marked
    `review_status: dismissed` in frontmatter.

    Each fragment dict carries the fields the review UI needs: filename,
    commit subject, short SHA, project, confidence, rationale, date.
    """
    result: dict[str, list[dict]] = {}
    dismissed_count = 0
    unclassified_dir = ENGINEERING_DIR / "unclassified"
    if not unclassified_dir.exists():
        return {"groups": [], "dismissed_count": 0, "pending_count": 0}

    for f in sorted(unclassified_dir.glob("*.md")):
        if f.name in ("_index.md", "index.md", "README.md"):
            continue
        fm = _read_frontmatter_only(f)
        if fm.get("review_status") == "dismissed":
            dismissed_count += 1
            continue
        project = fm.get("source_project", "unknown")
        result.setdefault(project, []).append({
            "filename": f.name,
            "title": fm.get("title", f.stem),
            "short_sha": fm.get("commit_short_sha", ""),
            "project": project,
            "date": _coerce_date_str(fm.get("source_date")),
            "confidence": fm.get("classification_confidence", ""),
            "rationale": fm.get("classification_rationale", ""),
            "files_changed": fm.get("files_changed", 0),
            "insertions": fm.get("insertions", 0),
            "deletions": fm.get("deletions", 0),
        })

    # Sort each project's fragments by date (newest first) and the
    # project list by total fragment count (largest first).
    groups = []
    pending = 0
    for proj in sorted(result.keys(), key=lambda p: -len(result[p])):
        items = sorted(result[proj], key=lambda r: r["date"], reverse=True)
        pending += len(items)
        groups.append({"project": proj, "count": len(items), "items": items})

    return {"groups": groups, "dismissed_count": dismissed_count, "pending_count": pending}


def _safe_fragment_path(filename: str) -> Path | None:
    """Validate a filename is a safe relative path inside the unclassified
    directory. Returns the resolved Path if safe, None if not.
    """
    if not filename or "/" in filename or "\\" in filename or ".." in filename:
        return None
    p = (ENGINEERING_DIR / "unclassified" / filename).resolve()
    # Must stay inside ENGINEERING_DIR/unclassified
    try:
        p.relative_to((ENGINEERING_DIR / "unclassified").resolve())
    except ValueError:
        return None
    if not p.exists():
        return None
    return p


def _rewrite_fragment_frontmatter(path: Path, updates: dict) -> bool:
    """Surgically update the YAML frontmatter of a fragment in place.
    Adds or overwrites the keys in `updates`. Returns True on success.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    if not text.startswith("---"):
        return False
    try:
        end = text.index("\n---\n", 4)
    except ValueError:
        return False
    try:
        fm = yaml.safe_load(text[4:end]) or {}
    except yaml.YAMLError:
        return False
    if not isinstance(fm, dict):
        return False
    fm.update(updates)
    new_fm = yaml.safe_dump(fm, sort_keys=False, default_flow_style=False, allow_unicode=True).strip()
    body = text[end + 5 :]
    path.write_text(f"---\n{new_fm}\n---\n{body}", encoding="utf-8")
    return True


@app.route("/review/unclassified/assign", methods=["POST"])
def assign_unclassified():
    """Move a fragment from wiki/engineering/unclassified/ into a target
    engineering topic directory. Updates topic_slug in frontmatter and
    records that the assignment was manual."""
    _validate_csrf()
    filename = (request.form.get("filename") or "").strip()
    target = (request.form.get("target") or "").strip()
    src = _safe_fragment_path(filename)
    if src is None:
        return "invalid filename", 400
    if target == "unclassified" or target not in ENGINEERING_TOPIC_NAMES:
        return "invalid target topic", 400

    target_dir = ENGINEERING_DIR / target
    target_dir.mkdir(parents=True, exist_ok=True)
    dst = target_dir / filename

    # Update frontmatter first, then move
    _rewrite_fragment_frontmatter(src, {
        "topic_slug": target,
        "classification_confidence": "manual",
        "classification_rationale": f"manually reassigned to {target} via review UI",
    })
    import shutil as _shutil
    try:
        _shutil.move(str(src), str(dst))
    except Exception as e:
        return f"move failed: {e}", 500
    return redirect_to_review()


@app.route("/review/unclassified/dismiss", methods=["POST"])
def dismiss_unclassified():
    """Mark a fragment as permanently unclassified so it stops
    appearing in the review queue. Writes `review_status: dismissed`
    into frontmatter; the file stays in wiki/engineering/unclassified/."""
    _validate_csrf()
    filename = (request.form.get("filename") or "").strip()
    src = _safe_fragment_path(filename)
    if src is None:
        return "invalid filename", 400
    _rewrite_fragment_frontmatter(src, {
        "review_status": "dismissed",
    })
    return redirect_to_review()


def redirect_to_review():
    from flask import redirect
    return redirect("/review/taxonomy#unclassified")


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
        _validate_csrf()
        slug = (request.form.get("slug") or "").strip()
        industry = (request.form.get("industry") or "").strip()
        if slug and industry:
            if _rewrite_client_industry(slug, industry):
                message = f"Assigned {industry} to {slug}"
            else:
                message = f"No change — {slug} already has this industry or was not found"

    rows = _parse_clients_yaml_for_review()

    # Strip meta-entries like `_internal` that represent "not a real client"
    # buckets in the registry. They have no meaningful industry, can't be
    # assigned one, and clutter the review queue. Anything whose slug
    # starts with `_` or whose status is `internal` is filtered out here.
    rows = [
        r for r in rows
        if not r["slug"].startswith("_") and r.get("status") != "internal"
    ]

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

    # Unclassified engineering commits — pending human review
    unclassified_review = _collect_unclassified_for_review()
    # Build list of engineering topics for the assign dropdown. Exclude
    # `unclassified` itself — can't reassign to the same bucket.
    engineering_topics_picker = [
        {"slug": slug, "name": name}
        for slug, name in sorted(
            ENGINEERING_TOPIC_NAMES.items(), key=lambda x: x[1]
        )
        if slug != "unclassified"
    ]

    return render_template(
        "review_taxonomy.html",
        needs_review=needs_review,
        ok_count=len(ok_rows),
        total=len(rows),
        industries=industries_for_picker,
        empty_industries=empty_industries,
        unclassified_review=unclassified_review,
        engineering_topics_picker=engineering_topics_picker,
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
    filepath = _safe_resolve(article_path)
    if filepath is None:
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

    # Layer 4 references — patterns/emergence/contradictions that link this industry
    layer4_refs = _find_layer4_references("industries", slug)
    for ref in layer4_refs:
        ref["topic_labels"] = [
            (ns, s, TOPIC_NAMES.get(s, s.replace("-", " ").title()))
            for ns, s in _topics_connected_slugs(ref)
        ]

    # Reuse the topic template — same shape, same rendering needs
    return render_template(
        "topic.html",
        slug=slug,
        topic_name=industry_name,
        articles=articles,
        synthesis=synthesis,
        synthesis_html=synthesis_html,
        clients_on_topic=clients_on_topic,
        layer4_refs=layer4_refs,
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

    # Layer 4 references — patterns/emergence/contradictions that link this topic
    layer4_refs = _find_layer4_references("knowledge", slug)
    for ref in layer4_refs:
        ref["topic_labels"] = [
            (ns, s, TOPIC_NAMES.get(s, s.replace("-", " ").title()))
            for ns, s in _topics_connected_slugs(ref)
        ]

    return render_template(
        "topic.html",
        slug=slug,
        topic_name=topic_name,
        articles=articles,
        synthesis=synthesis,
        synthesis_html=synthesis_html,
        clients_on_topic=clients_on_topic,
        layer4_refs=layer4_refs,
    )


@app.route("/engineering/")
def engineering_index():
    """Engineering topics browse page — companion to the /topic/ route family.

    Projects moved to their own /projects/ page for navigation symmetry, so
    this page is now topics-only. `projects` is still passed to the template
    for legacy compatibility but the template no longer renders it.
    """
    engineering_topics = _load_engineering_topics_with_counts()
    stats = get_stats()
    return render_template(
        "engineering_index.html",
        engineering_topics=engineering_topics,
        stats=stats,
    )


@app.route("/knowledge/")
def knowledge_index():
    """Business knowledge topics browse page."""
    topics = _collect_business_topics()
    stats = get_stats()
    return render_template(
        "knowledge_index.html",
        topics=topics,
        stats=stats,
    )


@app.route("/industries/")
def industries_index():
    """Industries browse page — parallel to engineering/interests indexes."""
    industries = _collect_industries()
    stats = get_stats()
    return render_template(
        "industries_index.html",
        industries=industries,
        stats=stats,
    )


@app.route("/clients/")
def clients_index():
    """Clients browse page — parallel to the other namespace indexes."""
    clients = _collect_clients()
    stats = get_stats()
    return render_template(
        "clients_index.html",
        clients=clients,
        stats=stats,
    )


@app.route("/projects/")
def projects_index():
    """Projects browse page — parallel to engineering topics."""
    projects = _load_projects_with_counts()
    stats = get_stats()
    return render_template(
        "projects_index.html",
        projects=projects,
        stats=stats,
    )


def _collect_business_topics() -> list[dict]:
    """Walk wiki/knowledge/ and return browse data for each topic directory."""
    result = []
    knowledge_dir = WIKI_DIR / "knowledge"
    if not knowledge_dir.exists():
        return result
    for d in sorted(knowledge_dir.iterdir()):
        if not d.is_dir():
            continue
        frag_count = sum(
            1 for f in d.rglob("*.md")
            if f.name not in ("_index.md", "index.md")
        )
        has_l3 = False
        confidence = ""
        evidence_count = 0
        index_file = d / "index.md"
        if index_file.exists():
            try:
                content = index_file.read_text(encoding="utf-8", errors="replace")
                if "layer: 3" in content:
                    has_l3 = True
                    fm, _ = parse_frontmatter(content)
                    confidence = fm.get("confidence", "")
                    evidence_count = fm.get("evidence_count", 0)
            except Exception:
                pass
        result.append({
            "slug": d.name,
            "name": TOPIC_NAMES.get(d.name, d.name.replace("-", " ").title()),
            "articles": frag_count,
            "layer3": has_l3,
            "confidence": confidence,
            "evidence_count": evidence_count,
        })
    result.sort(key=lambda x: (not x["layer3"], -x["articles"]))
    return result


def _collect_industries() -> list[dict]:
    """Return browse data for all industry directories."""
    result = []
    industries_dir = WIKI_DIR / "industries"
    if not industries_dir.exists():
        return result
    # Load display names from industries.yaml so the page matches the
    # dashboard card labels instead of showing raw slugs.
    name_by_slug: dict[str, str] = {}
    industries_yaml = MERIDIAN_ROOT / "industries.yaml"
    if industries_yaml.exists():
        try:
            data = yaml.safe_load(industries_yaml.read_text(encoding="utf-8")) or {}
            for entry in data.get("industries", []):
                if isinstance(entry, dict) and entry.get("slug"):
                    name_by_slug[entry["slug"]] = entry.get("name", entry["slug"])
        except yaml.YAMLError:
            pass
    for d in sorted(industries_dir.iterdir()):
        if not d.is_dir():
            continue
        real_fragments = [
            f for f in d.glob("*.md")
            if f.name not in ("_index.md", "index.md", "PLACEHOLDER.md")
        ]
        is_placeholder = not real_fragments and (d / "PLACEHOLDER.md").exists()
        has_l3 = False
        index_file = d / "index.md"
        if index_file.exists():
            try:
                if "layer: 3" in index_file.read_text(encoding="utf-8", errors="replace"):
                    has_l3 = True
            except Exception:
                pass
        result.append({
            "slug": d.name,
            "name": name_by_slug.get(d.name, d.name.replace("-", " ").title()),
            "fragment_count": len(real_fragments),
            "layer3": has_l3,
            "placeholder": is_placeholder,
        })
    result.sort(key=lambda x: (x["placeholder"], not x["layer3"], -x["fragment_count"]))
    return result


def _collect_clients() -> list[dict]:
    """Return browse data for all current clients."""
    result = []
    clients_dir = WIKI_DIR / "clients" / "current"
    if not clients_dir.exists():
        return result
    for d in sorted(clients_dir.iterdir()):
        if not d.is_dir():
            continue
        topic_count = 0
        insight_count = 0
        index_file = d / "_index.md"
        if index_file.exists():
            try:
                content = index_file.read_text(encoding="utf-8", errors="replace")
                topic_links = re.findall(r"\[\[knowledge/[^|\]]+", content)
                topic_count = len(set(topic_links))
                insight_nums = re.findall(r"\((\d+)\s+insights?\)", content)
                insight_count = sum(int(n) for n in insight_nums)
            except Exception:
                pass
        result.append({
            "slug": d.name,
            "name": CLIENT_NAMES.get(d.name, d.name.replace("-", " ").title()),
            "topic_count": topic_count,
            "insight_count": insight_count,
        })
    result.sort(key=lambda x: -x["insight_count"])
    return result


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


@app.route("/interests/")
def interests_index():
    """Interests topics browse page."""
    interests_topics = _load_interests_topics_with_counts()
    stats = get_stats()
    return render_template(
        "interests_index.html",
        interests_topics=interests_topics,
        stats=stats,
    )


@app.route("/interests/<slug>")
def view_interests_topic(slug):
    """Render a single interests topic page."""
    topic_dir = INTERESTS_DIR / slug
    if not topic_dir.exists():
        # The directory may not exist yet if nothing has been filed there.
        # Render an empty-state page instead of 404 so you can still navigate.
        topic_name = INTERESTS_TOPIC_NAMES.get(slug, slug.replace("-", " ").title())
        return render_template(
            "interests_topic.html",
            slug=slug,
            topic_name=topic_name,
            articles=[],
            synthesis=None,
            synthesis_html="",
            sources_on_topic=[],
        )

    topic_name = INTERESTS_TOPIC_NAMES.get(slug, slug.replace("-", " ").title())

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

    articles = []
    for f in sorted(topic_dir.rglob("*.md")):
        if f.name in ("_index.md", "index.md", "README.md"):
            continue
        articles.append(_enrich_interests_fragment(read_article(f)))
    articles.sort(key=lambda a: a.get("sort_date", ""), reverse=True)

    # Distinct source set for filter chips (books/articles/reflections)
    sources_on_topic = sorted(
        {a["source_display"] for a in articles if a.get("source_display")}
    )

    return render_template(
        "interests_topic.html",
        slug=slug,
        topic_name=topic_name,
        articles=articles,
        synthesis=synthesis,
        synthesis_html=synthesis_html,
        sources_on_topic=sources_on_topic,
    )


def _enrich_interests_fragment(article: dict) -> dict:
    """Enrich an interests fragment with source display metadata."""
    fm = article.get("frontmatter", {}) or {}
    source_type = fm.get("source_type", "")
    source_author = fm.get("source_author", "")
    # Display label: author if we have one, else source_type tag
    source_display = source_author or source_type.replace("external-", "").replace("internal-", "")
    article["source_display"] = source_display
    article["source_type"] = source_type
    article["source_date"] = fm.get("source_date", "")
    article["sort_date"] = str(fm.get("source_date", ""))[:10] if fm.get("source_date") else ""
    article["title"] = fm.get("title") or article.get("title", "")
    body = article.get("body", "")
    preview = body[:300].replace("\n", " ").strip()
    article["preview"] = preview
    article["word_count"] = len(body.split())
    path = article.get("path", "")
    if isinstance(path, Path):
        try:
            article["path"] = path.relative_to(MERIDIAN_ROOT).as_posix()
        except ValueError:
            article["path"] = str(path)
    return article


def _load_layer4_articles() -> list[dict]:
    """Walk wiki/layer4/ and return all Layer 4 articles with their frontmatter.
    Used by /concepts and /concepts/stats.
    """
    result = []
    if not LAYER4_DIR.exists():
        return result
    for subdir_name, concept_type in (
        ("patterns",       "pattern"),
        ("emergence",      "emergence"),
        ("contradictions", "contradiction"),
        ("drift",          "drift"),
    ):
        subdir = LAYER4_DIR / subdir_name
        if not subdir.exists():
            continue
        for f in sorted(subdir.glob("*.md")):
            if f.name == "_index.md":
                continue
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
            if not isinstance(fm, dict) or fm.get("layer") != 4:
                continue
            body = text[end + 5 :]
            result.append({
                "path": f.relative_to(MERIDIAN_ROOT).as_posix(),
                "slug": f.stem,
                "concept_type": fm.get("concept_type") or concept_type,
                "title": fm.get("title") or f.stem,
                "topics_connected": fm.get("topics_connected") or [],
                "industries_connected": fm.get("industries_connected") or [],
                "confidence": fm.get("confidence") or "low",
                "hypothesis": bool(fm.get("hypothesis", True)),
                "status": fm.get("status") or "active",
                "first_detected": str(fm.get("first_detected") or "")[:10],
                "last_updated": str(fm.get("last_updated") or "")[:10],
                "supporting_evidence_count": int(fm.get("supporting_evidence_count") or 0),
                "contradicting_evidence_count": int(fm.get("contradicting_evidence_count") or 0),
                "decision_rule": fm.get("decision_rule"),
                "body": body,
            })
    return result


def _layer4_summary_from(articles: list[dict]) -> dict:
    """Collapse Layer 4 article list into a summary dict for dashboard stats."""
    active_patterns = sum(
        1 for a in articles
        if a["concept_type"] == "pattern" and not a["hypothesis"] and a["status"] == "active"
    )
    emerging = sum(
        1 for a in articles
        if a["concept_type"] == "pattern" and a["hypothesis"]
    )
    contradictions_resolved = sum(
        1 for a in articles
        if a["concept_type"] == "contradiction" and a["status"] == "resolved"
    )
    contradictions_unresolved = sum(
        1 for a in articles
        if a["concept_type"] == "contradiction" and a["status"] == "unresolved"
    )
    drift = sum(1 for a in articles if a["concept_type"] == "drift")
    by_confidence = defaultdict(int)
    for a in articles:
        by_confidence[a["confidence"]] += 1
    return {
        "active_patterns": active_patterns,
        "emerging": emerging,
        "contradictions_resolved": contradictions_resolved,
        "contradictions_unresolved": contradictions_unresolved,
        "drift": drift,
        "total": len(articles),
        "by_confidence": dict(by_confidence),
    }


def _find_layer4_references(namespace: str, slug: str) -> list[dict]:
    """Return Layer 4 articles whose topics_connected references this
    (namespace, slug) pair. Used by view_topic() and view_industry() to
    render the "Layer 4 Connections" section on Layer 3 pages.

    namespace is one of "knowledge" or "industries".
    """
    articles = _load_layer4_articles()
    matches: list[dict] = []
    target_fragment = f"wiki/{namespace}/{slug}/"
    for article in articles:
        for entry in article.get("topics_connected", []) + article.get("industries_connected", []):
            if not isinstance(entry, str):
                continue
            if entry.startswith(target_fragment) or f"/{slug}/" in entry:
                # Double-check the namespace segment matches
                parts = entry.strip("/").split("/")
                if len(parts) >= 3 and parts[0] == "wiki" and parts[1] == namespace and parts[2] == slug:
                    matches.append(article)
                    break
    # Sort: established first, then emerging, by first_detected desc
    def _key(a):
        status_rank = 0 if (not a["hypothesis"] and a["status"] == "active") else 1
        return (status_rank, -int(a["first_detected"].replace("-", "") or 0))
    matches.sort(key=_key)
    return matches


def _topics_connected_slugs(article: dict) -> list[tuple[str, str]]:
    """Extract (namespace, slug) from each topics_connected path."""
    result = []
    for entry in article.get("topics_connected", []):
        if not isinstance(entry, str):
            continue
        parts = entry.strip("/").split("/")
        if len(parts) >= 3 and parts[0] == "wiki" and parts[1] in ("knowledge", "industries"):
            result.append((parts[1], parts[2]))
    return result


@app.route("/concepts/")
@app.route("/concepts")
def concepts_page():
    """Layer 4 conceptual knowledge browse page."""
    articles = _load_layer4_articles()
    summary = _layer4_summary_from(articles)

    active_patterns = [
        a for a in articles
        if a["concept_type"] == "pattern" and not a["hypothesis"] and a["status"] == "active"
    ]
    active_patterns.sort(key=lambda x: x["first_detected"], reverse=True)

    emerging = [
        a for a in articles
        if a["concept_type"] == "pattern" and a["hypothesis"]
    ]
    emerging.sort(key=lambda x: x["first_detected"], reverse=True)

    resolved = [
        a for a in articles
        if a["concept_type"] == "contradiction" and a["status"] == "resolved"
    ]
    resolved.sort(key=lambda x: x["first_detected"], reverse=True)

    unresolved = [
        a for a in articles
        if a["concept_type"] == "contradiction" and a["status"] == "unresolved"
    ]
    unresolved.sort(key=lambda x: x["first_detected"], reverse=True)

    drift = [a for a in articles if a["concept_type"] == "drift"]
    drift.sort(key=lambda x: x["first_detected"], reverse=True)

    # Add human-readable topic names for display
    def _name_topics(items):
        for a in items:
            a["topic_labels"] = [
                (ns, slug, TOPIC_NAMES.get(slug, slug.replace("-", " ").title()))
                for ns, slug in _topics_connected_slugs(a)
            ]
    _name_topics(active_patterns)
    _name_topics(emerging)
    _name_topics(resolved)
    _name_topics(unresolved)
    _name_topics(drift)

    return render_template(
        "concepts.html",
        summary=summary,
        active_patterns=active_patterns,
        emerging=emerging,
        resolved=resolved,
        unresolved=unresolved,
        drift=drift,
    )


@app.route("/concepts/stats")
def concepts_stats():
    """JSON stats for the CLI `meridian conceptualize --status` command."""
    articles = _load_layer4_articles()
    summary = _layer4_summary_from(articles)
    return jsonify({"status": "ok", "summary": summary})


REPORT_CATEGORIES = [
    {
        "key": "lint",
        "title": "Wiki Health Checks",
        "description": "Weekly linter scans across all five namespaces — contradictions, orphans, gaps, registry drift, and suggested connections.",
        "schedule": "Sunday 06:00 UTC",
    },
    {
        "key": "evolution",
        "title": "Knowledge Evolution",
        "description": "Weekly evolution detector scans — contradiction accumulation, source date divergence, platform triggers, and confidence decay across Layer 3 articles.",
        "schedule": "Sunday 07:00 UTC",
    },
    {
        "key": "layer4",
        "title": "Conceptual Analysis",
        "description": "Monthly Layer 4 reports — cross-topic pattern discovery, contradiction resolution, and pattern maturation summaries.",
        "schedule": "1st Sunday of month 10:00 UTC",
    },
]


@app.route("/admin/")
@app.route("/admin")
def admin_page():
    """System administration — monitoring, schedules, triggers, and configuration."""
    # Wiki stats (reuse existing)
    stats = get_stats()

    # Admin stats from the collector script (system, containers, git, backup)
    admin_stats: dict = {}
    admin_stats_path = MERIDIAN_ROOT / "state" / "admin-stats.json"
    if admin_stats_path.exists():
        try:
            admin_stats = json.loads(admin_stats_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            admin_stats = {}

    # Synthesis queue
    synth_queue: dict = {"pending": 0, "complete": 0, "running": 0, "failed": 0, "total": 0}
    queue_path = MERIDIAN_ROOT / "synthesis_queue.json"
    if queue_path.exists():
        try:
            items = json.loads(queue_path.read_text(encoding="utf-8"))
            if isinstance(items, list):
                for item in items:
                    s = item.get("status", "pending") if isinstance(item, dict) else "unknown"
                    if s in synth_queue:
                        synth_queue[s] += 1
                synth_queue["total"] = len(items)
        except (json.JSONDecodeError, OSError):
            pass

    # Config summary — key values from config.yaml
    config_summary: dict = {}
    config_path = MERIDIAN_ROOT / "config.yaml"
    if config_path.exists():
        try:
            cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            config_summary = {
                "llm_model": cfg.get("llm", {}).get("model", "?"),
                "planning_model": cfg.get("compiler", {}).get("planning_model", "?"),
                "writing_model": cfg.get("compiler", {}).get("writing_model", "?"),
                "distill_min_relevance": cfg.get("distill", {}).get("min_relevance", "?"),
                "distill_min_quality": cfg.get("distill", {}).get("min_quality", "?"),
                "synthesis_per_day": cfg.get("synthesis", {}).get("synthesis_per_day", "?"),
                "stale_threshold_days": cfg.get("evolution", {}).get("stale_threshold_days", "?"),
            }
        except yaml.YAMLError:
            pass

    # Layer 4 summary
    layer4_articles = _load_layer4_articles()
    layer4_summary = _layer4_summary_from(layer4_articles)

    # Schedule — hardcoded since we know the full schedule
    schedule = [
        {"time": "01:00", "job": "Daily Distill", "type": "n8n", "freq": "daily",
         "endpoint": "/distill", "method": "POST"},
        {"time": "02:00", "job": "Daily Compile", "type": "n8n", "freq": "daily",
         "endpoint": "/compile", "method": "POST"},
        {"time": "03:00", "job": "Backup (restic)", "type": "cron", "freq": "daily",
         "endpoint": None, "method": None},
        {"time": "04:00", "job": "Daily Synthesize", "type": "n8n", "freq": "daily",
         "endpoint": "/synthesize/schedule", "method": "POST"},
        {"time": "05:00", "job": "Mode C (emergence)", "type": "n8n", "freq": "daily",
         "endpoint": "/conceptualize", "method": "POST", "body": '{"mode":"emergence"}'},
        {"time": "06:00", "job": "Weekly Lint", "type": "cron", "freq": "Sunday",
         "endpoint": "/lint", "method": "POST"},
        {"time": "07:00", "job": "Evolution Detector", "type": "cron", "freq": "Sunday",
         "endpoint": None, "method": None},
        {"time": "08:00", "job": "Mode A + B (connections + maturation)", "type": "n8n", "freq": "Sunday",
         "endpoint": "/conceptualize", "method": "POST", "body": '{"mode":"connections"}'},
        {"time": "10:00", "job": "Mode D (contradictions)", "type": "n8n", "freq": "1st Sunday",
         "endpoint": "/conceptualize", "method": "POST", "body": '{"mode":"contradictions"}'},
        {"time": ":15", "job": "Hourly Watchdog", "type": "n8n", "freq": "hourly",
         "endpoint": "/watchdog", "method": "POST"},
    ]

    # Recent reports for the inline log viewer
    report_groups: list[dict] = []
    for cat in REPORT_CATEGORIES:
        subdir = REPORTS_DIR / cat["key"]
        files: list[dict] = []
        if subdir.exists():
            raw = sorted(
                [f for f in subdir.glob("*.md") if f.name not in ("README.md", ".gitkeep")],
                key=lambda f: f.name,
                reverse=True,
            )
            files = [
                {
                    "name": f.name,
                    "path": f.relative_to(MERIDIAN_ROOT).as_posix(),
                    "size": f.stat().st_size,
                    "date": f.name.split("-", 1)[-1].replace(".md", "") if "-" in f.name else "",
                }
                for f in raw
            ]
        report_groups.append({**cat, "count": len(files), "files": files})

    return render_template(
        "admin.html",
        stats=stats,
        admin_stats=admin_stats,
        synth_queue=synth_queue,
        config_summary=config_summary,
        layer4_summary=layer4_summary,
        schedule=schedule,
        report_groups=report_groups,
    )


@app.route("/admin/stats.json")
def admin_stats_json():
    """Live admin stats for auto-refresh polling."""
    stats = get_stats()
    admin_stats: dict = {}
    admin_stats_path = MERIDIAN_ROOT / "state" / "admin-stats.json"
    if admin_stats_path.exists():
        try:
            admin_stats = json.loads(admin_stats_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    synth_queue: dict = {"pending": 0, "complete": 0, "running": 0, "failed": 0}
    queue_path = MERIDIAN_ROOT / "synthesis_queue.json"
    if queue_path.exists():
        try:
            items = json.loads(queue_path.read_text(encoding="utf-8"))
            if isinstance(items, list):
                for item in items:
                    s = item.get("status", "pending") if isinstance(item, dict) else "unknown"
                    if s in synth_queue:
                        synth_queue[s] += 1
        except (json.JSONDecodeError, OSError):
            pass
    return jsonify({
        "system": admin_stats.get("system", {}),
        "git": admin_stats.get("git", {}),
        "backup": admin_stats.get("backup", {}),
        "deploy": admin_stats.get("deploy", {}),
        "generated_at": admin_stats.get("generated_at", ""),
        "synth_queue": synth_queue,
        "capture_queue": stats.get("capture", 0),
        "raw_sources": stats.get("raw", 0),
    })


@app.route("/admin/report/<path:report_path>")
def admin_view_report(report_path):
    """Render a report file inline as HTML for the admin log viewer."""
    filepath = _safe_resolve(f"reports/{report_path}")
    if filepath is None:
        filepath = _safe_resolve(f"wiki/articles/{report_path}")
    if filepath is None:
        return jsonify({"error": "not found"}), 404
    try:
        content = filepath.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return jsonify({"error": "read failed"}), 500
    html = render_markdown(content)
    return jsonify({"html": html, "path": str(filepath.relative_to(MERIDIAN_ROOT))})


@app.route("/admin/trigger", methods=["POST"])
def admin_trigger():
    """Proxy trigger requests to the receiver with auth."""
    _validate_csrf()
    data = request.get_json(force=True) if request.data else {}
    endpoint = data.get("endpoint", "")
    body = data.get("body", "{}")
    if not endpoint or not endpoint.startswith("/"):
        return jsonify({"error": "invalid endpoint"}), 400
    try:
        resp = requests.post(
            f"{RECEIVER_URL}{endpoint}",
            headers=receiver_headers(),
            json=json.loads(body) if isinstance(body, str) else body,
            timeout=30,
        )
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route("/admin/job/<job_id>")
def admin_job_status(job_id):
    """Proxy job status polling to the receiver."""
    try:
        resp = requests.get(
            f"{RECEIVER_URL}/jobs/{job_id}",
            headers=receiver_headers(),
            timeout=10,
        )
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route("/reports/")
@app.route("/reports")
def reports_page():
    """Browse all operational reports generated by Meridian agents."""
    report_groups: list[dict] = []
    for cat in REPORT_CATEGORIES:
        subdir = REPORTS_DIR / cat["key"]
        files: list[dict] = []
        if subdir.exists():
            raw = sorted(
                [f for f in subdir.glob("*.md") if f.name not in ("README.md", ".gitkeep")],
                key=lambda f: f.name,
                reverse=True,
            )
            files = [
                {
                    "name": f.name,
                    "path": f.relative_to(MERIDIAN_ROOT).as_posix(),
                    "size": f.stat().st_size,
                    "date": f.name.split("-", 1)[-1].replace(".md", "") if "-" in f.name else "",
                }
                for f in raw
            ]
        report_groups.append({
            **cat,
            "count": len(files),
            "files": files,
        })
    return render_template("reports.html", report_groups=report_groups)


@app.route("/reports/<category>/")
def reports_category(category):
    """Full list of reports in a single category."""
    cat = next((c for c in REPORT_CATEGORIES if c["key"] == category), None)
    if cat is None:
        return "Report category not found", 404
    subdir = REPORTS_DIR / category
    files: list[dict] = []
    if subdir.exists():
        raw = sorted(
            [f for f in subdir.glob("*.md") if f.name not in ("README.md", ".gitkeep")],
            key=lambda f: f.name,
            reverse=True,
        )
        files = [
            {
                "name": f.name,
                "path": f.relative_to(MERIDIAN_ROOT).as_posix(),
                "size": f.stat().st_size,
                "date": f.name.split("-", 1)[-1].replace(".md", "") if "-" in f.name else "",
            }
            for f in raw
        ]
    return render_template("reports_category.html", cat=cat, files=files)


@app.route("/analytics/")
def analytics_page():
    """Deep insights into the data — provenance, density, freshness, gaps."""
    analytics = _compute_analytics()
    return render_template("analytics.html", analytics=analytics)


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


def _fragment_date(fm: dict) -> str:
    """Return the most meaningful date from a fragment's frontmatter as a
    YYYY-MM-DD string, or "" if no date field is set.

    Priority order: source_date (engineering), updated, created, first_seen.
    Handles both str and datetime.date forms (PyYAML parses dates eagerly).
    """
    for key in ("source_date", "updated", "created", "first_seen"):
        v = fm.get(key)
        if v:
            s = _coerce_date_str(v)
            if s:
                return s
    return ""


def _compute_analytics() -> dict:
    """Walk wiki/ and capture/ and build Meridian-specific analytics.

    Six sections: overview, synthesis coverage, density, clients, freshness,
    and session notes. Designed around "what synthesis work to do next"
    rather than generic knowledge-base metrics. Recomputes on every request
    (~1-2s on ~4,200 files).
    """
    from datetime import timedelta
    today = datetime.now().date()

    # Namespaces we walk. Business content lives in several top-level dirs;
    # the small misc buckets (hiring, operations, org, etc.) roll up into
    # "other" so they're visible but don't clutter the main view.
    namespace_dirs = [
        ("knowledge",   WIKI_DIR / "knowledge"),
        ("industries",  WIKI_DIR / "industries"),
        ("engineering", WIKI_DIR / "engineering"),
        ("interests",   WIKI_DIR / "interests"),
    ]
    # Session notes live in wiki/articles/ and wiki/concepts/, with
    # other small buckets rolled up separately.
    session_notes_dirs = [
        ("session-notes", WIKI_DIR / "articles"),
        ("concepts",      WIKI_DIR / "concepts"),
    ]
    misc_namespaces = [
        "hiring", "internal-tools", "operations", "org", "partnerships",
        "projects", "prospects", "team", "tools-internal", "vendors",
    ]

    fragments: list[dict] = []

    def harvest(namespace: str, directory: Path) -> None:
        if not directory.exists():
            return
        for f in directory.rglob("*.md"):
            if f.name in ("_index.md", "README.md", "PLACEHOLDER.md"):
                continue
            fm = _read_frontmatter_only(f)
            layer = fm.get("layer", 2)
            is_synthesis = f.name == "index.md" and layer == 3
            fragments.append({
                "path": f.relative_to(MERIDIAN_ROOT).as_posix(),
                "namespace": namespace,
                "is_synthesis": is_synthesis,
                "layer": layer,
                "date": _fragment_date(fm),
                "fm": fm,
            })

    for ns, d in namespace_dirs:
        harvest(ns, d)
    for ns, d in session_notes_dirs:
        harvest(ns, d)
    for bucket in misc_namespaces:
        harvest("other", WIKI_DIR / bucket)

    # Unclassified engineering commits sitting in capture/
    if COMMITS_CAPTURE_DIR.exists():
        for f in COMMITS_CAPTURE_DIR.rglob("*.md"):
            fm = _read_frontmatter_only(f)
            fragments.append({
                "path": f.relative_to(MERIDIAN_ROOT).as_posix(),
                "namespace": "unclassified",
                "is_synthesis": False,
                "layer": 2,
                "date": _fragment_date(fm),
                "fm": fm,
            })

    # -----------------------------------------------------------------
    # Per-namespace + per-topic aggregates (shared across sections)
    # -----------------------------------------------------------------
    by_namespace: dict[str, int] = defaultdict(int)
    topic_counts: dict[tuple[str, str], int] = defaultdict(int)
    topic_latest_date: dict[tuple[str, str], str] = {}
    l3_by_namespace: dict[str, list[dict]] = defaultdict(list)

    for frag in fragments:
        ns = frag["namespace"]
        parts = frag["path"].split("/")
        # Only "real" namespaces produce topic entries (ns/topic/file.md)
        is_topicful = ns in ("knowledge", "industries", "engineering", "interests")

        if frag["is_synthesis"]:
            # Collect L3 metadata for the synthesis-coverage section.
            #
            # `synthesis_date` prefers `generated_at` (code-injected
            # provenance timestamp, always the actual synthesis time)
            # over `last_updated` (which the LLM writes based on the
            # latest source date in the corpus — historically ambiguous
            # and produces nonsense staleness numbers for topics whose
            # source material spans years before synthesis day).
            if len(parts) >= 3:
                slug = parts[2]
                fm = frag["fm"]
                generated_at = fm.get("generated_at")
                if isinstance(generated_at, str) and len(generated_at) >= 10:
                    synth_date = generated_at[:10]
                else:
                    synth_date = _coerce_date_str(fm.get("last_updated"))
                l3_by_namespace[ns].append({
                    "namespace": ns,
                    "slug": slug,
                    "fragment_count": fm.get("fragment_count") or fm.get("evidence_count") or 0,
                    "synthesis_date": synth_date,
                    "last_updated": _coerce_date_str(fm.get("last_updated")),
                    "confidence": fm.get("confidence", ""),
                })
            # L3 index files don't count as fragments in the density views
            continue

        by_namespace[ns] += 1

        if is_topicful and len(parts) >= 3 and parts[0] == "wiki":
            slug = parts[2]
            topic_counts[(ns, slug)] += 1
            d = frag["date"]
            if d:
                existing = topic_latest_date.get((ns, slug), "")
                if d > existing:
                    topic_latest_date[(ns, slug)] = d

    total_fragments = sum(by_namespace.values())

    # -----------------------------------------------------------------
    # SECTION 1: Knowledge Health (overview)
    # -----------------------------------------------------------------
    # Topics registered (from YAML) vs topics with fragments (from disk).
    # Exclude topics flagged `synthesize: false` from the L3 denominator
    # so the coverage % reflects "topics we intend to synthesize" rather
    # than "every topic in the registry."
    non_synth_engineering = sum(1 for ns, _ in _non_synthesizable_topic_slugs() if ns == "engineering")
    non_synth_interests = sum(1 for ns, _ in _non_synthesizable_topic_slugs() if ns == "interests")
    registered_topics = {
        "knowledge":   len(TOPIC_NAMES),
        "engineering": len(ENGINEERING_TOPIC_NAMES) - non_synth_engineering,
        "interests":   len(INTERESTS_TOPIC_NAMES) - non_synth_interests,
    }
    # industries isn't a TOPIC_NAMES-style global; read from the YAML directly
    industries_yaml = MERIDIAN_ROOT / "industries.yaml"
    if industries_yaml.exists():
        try:
            idata = yaml.safe_load(industries_yaml.read_text(encoding="utf-8")) or {}
            registered_topics["industries"] = len(idata.get("industries", []))
        except yaml.YAMLError:
            registered_topics["industries"] = 0
    else:
        registered_topics["industries"] = 0

    l3_count = sum(len(v) for v in l3_by_namespace.values())
    total_registered = sum(registered_topics.values())
    l3_coverage_pct = round(l3_count / total_registered * 100) if total_registered else 0

    topics_with_frags = len(topic_counts)
    median_per_topic = 0
    if topic_counts:
        sorted_counts = sorted(topic_counts.values())
        mid = len(sorted_counts) // 2
        median_per_topic = (
            sorted_counts[mid]
            if len(sorted_counts) % 2
            else (sorted_counts[mid - 1] + sorted_counts[mid]) // 2
        )

    # Find the stalest Layer 3 synthesis (oldest synthesis_date across
    # all namespaces). Uses synthesis_date — generated_at preferred
    # with last_updated fallback — so this number reflects "how long
    # since we last touched this article", not "how old is the most
    # recent fragment in it".
    all_l3 = [l3 for items in l3_by_namespace.values() for l3 in items]
    stalest_l3 = None
    stalest_l3_days = 0
    if all_l3:
        dated = [(l3, l3["synthesis_date"]) for l3 in all_l3 if l3.get("synthesis_date")]
        if dated:
            dated.sort(key=lambda x: x[1])
            oldest_l3, oldest_date = dated[0]
            try:
                oldest_dt = datetime.strptime(oldest_date, "%Y-%m-%d").date()
                stalest_l3_days = (today - oldest_dt).days
                stalest_l3 = oldest_l3
            except ValueError:
                pass

    # Most recent ingest date across all fragments
    most_recent_date = ""
    for frag in fragments:
        if frag["date"] and frag["date"] > most_recent_date:
            most_recent_date = frag["date"]

    overview = {
        "total_fragments": total_fragments,
        "topics_with_fragments": topics_with_frags,
        "total_registered_topics": total_registered,
        "l3_count": l3_count,
        "l3_coverage_pct": l3_coverage_pct,
        "median_fragments_per_topic": median_per_topic,
        "stalest_l3": stalest_l3,
        "stalest_l3_days": stalest_l3_days,
        "most_recent_date": most_recent_date,
    }

    # -----------------------------------------------------------------
    # SECTION 2: Synthesis Coverage — the actionable "what next"
    # -----------------------------------------------------------------
    # Per-namespace L3 coverage (count and percentage)
    per_ns_coverage = []
    for ns in ("knowledge", "industries", "engineering", "interests"):
        total = registered_topics.get(ns, 0)
        done = len(l3_by_namespace.get(ns, []))
        pct = round(done / total * 100) if total else 0
        per_ns_coverage.append({
            "namespace": ns,
            "registered": total,
            "synthesized": done,
            "pct": pct,
        })

    # Stale syntheses — L3 articles whose synthesis_date is > 60 days old.
    # Uses synthesis_date (generated_at preferred) so re-synthesis fresh
    # runs aren't immediately flagged as stale just because their source
    # corpus is old.
    stale_syntheses = []
    for l3 in all_l3:
        if not l3.get("synthesis_date"):
            continue
        try:
            d = datetime.strptime(l3["synthesis_date"], "%Y-%m-%d").date()
        except ValueError:
            continue
        age_days = (today - d).days
        if age_days > 60:
            stale_syntheses.append({**l3, "age_days": age_days})
    stale_syntheses.sort(key=lambda x: -x["age_days"])

    # Grown since synthesis — L3 articles where current fragment count
    # has grown >20% above the fragment_count stored at synthesis time
    grown_since = []
    for l3 in all_l3:
        key = (l3["namespace"], l3["slug"])
        current = topic_counts.get(key, 0)
        stored = int(l3.get("fragment_count", 0) or 0)
        if stored and current > 0:
            growth_pct = round((current - stored) / stored * 100)
            if growth_pct >= 20:
                grown_since.append({
                    "namespace": l3["namespace"],
                    "slug": l3["slug"],
                    "stored": stored,
                    "current": current,
                    "growth_pct": growth_pct,
                    "last_updated": l3.get("last_updated", ""),
                })
    grown_since.sort(key=lambda x: -x["growth_pct"])

    # Ready to synthesize — topics with 5+ fragments but no L3 yet.
    # Exclude topics flagged `synthesize: false` in the registry
    # (e.g., the `unclassified` catch-all in engineering-topics.yaml).
    synthesized_keys = {(l3["namespace"], l3["slug"]) for l3 in all_l3}
    non_synth = _non_synthesizable_topic_slugs()
    ready_to_synthesize = [
        {"namespace": ns, "slug": slug, "count": count}
        for (ns, slug), count in topic_counts.items()
        if count >= 5
        and (ns, slug) not in synthesized_keys
        and (ns, slug) not in non_synth
    ]
    ready_to_synthesize.sort(key=lambda x: -x["count"])

    synthesis = {
        "per_namespace": per_ns_coverage,
        "stale": stale_syntheses[:10],
        "stale_total": len(stale_syntheses),
        "grown": grown_since[:10],
        "grown_total": len(grown_since),
        "ready": ready_to_synthesize[:15],
        "ready_total": len(ready_to_synthesize),
    }

    # -----------------------------------------------------------------
    # SECTION 3: Density & Gaps
    # -----------------------------------------------------------------
    # Histogram
    buckets = {"1-4": 0, "5-19": 0, "20-49": 0, "50-99": 0, "100+": 0}
    for count in topic_counts.values():
        if count < 5:
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
    sparse_topics = sorted(
        [(k, v) for k, v in topic_counts.items() if 0 < v < 5],
        key=lambda x: x[1],
    )

    # Registry orphans — entries in *.yaml with 0 fragments on disk
    orphan_entries = []
    for slug in TOPIC_NAMES:
        if ("knowledge", slug) not in topic_counts:
            orphan_entries.append({"namespace": "knowledge", "slug": slug,
                                    "name": TOPIC_NAMES.get(slug, slug)})
    for slug in ENGINEERING_TOPIC_NAMES:
        if ("engineering", slug) not in topic_counts:
            orphan_entries.append({"namespace": "engineering", "slug": slug,
                                    "name": ENGINEERING_TOPIC_NAMES.get(slug, slug)})
    for slug in INTERESTS_TOPIC_NAMES:
        if ("interests", slug) not in topic_counts:
            orphan_entries.append({"namespace": "interests", "slug": slug,
                                    "name": INTERESTS_TOPIC_NAMES.get(slug, slug)})

    density = {
        "histogram": buckets,
        "top_topics": [
            {"namespace": ns, "slug": slug, "count": count}
            for (ns, slug), count in top_topics
        ],
        "sparse_topics": [
            {"namespace": ns, "slug": slug, "count": count}
            for (ns, slug), count in sparse_topics
        ],
        "orphan_registry_entries": orphan_entries,
        "unclassified_count": by_namespace.get("unclassified", 0),
        "by_namespace": {
            ns: by_namespace.get(ns, 0)
            for ns in ("knowledge", "industries", "engineering",
                       "interests", "session-notes", "concepts",
                       "other", "unclassified")
        },
    }

    # -----------------------------------------------------------------
    # SECTION 4: Client Density
    # -----------------------------------------------------------------
    # Parse client _index.md files for wikilinks + insight counts
    # (mirrors the dashboard client list logic).
    client_rows = []
    clients_dir = WIKI_DIR / "clients" / "current"
    if clients_dir.exists():
        for d in sorted(clients_dir.iterdir()):
            if not d.is_dir():
                continue
            topic_count = 0
            insight_count = 0
            index_file = d / "_index.md"
            if index_file.exists():
                try:
                    content = index_file.read_text(encoding="utf-8", errors="replace")
                    topic_links = re.findall(r"\[\[knowledge/[^|\]]+", content)
                    topic_count = len(set(topic_links))
                    insight_nums = re.findall(r"\((\d+)\s+insights?\)", content)
                    insight_count = sum(int(n) for n in insight_nums)
                except Exception:
                    pass
            client_rows.append({
                "slug": d.name,
                "name": CLIENT_NAMES.get(d.name, d.name.replace("-", " ").title()),
                "topic_count": topic_count,
                "insight_count": insight_count,
            })
    client_rows.sort(key=lambda x: -x["insight_count"])

    top_clients = client_rows[:10]
    empty_clients = [c for c in client_rows if c["insight_count"] == 0]

    # Industry fragment counts
    industry_fragments = []
    for (ns, slug), count in topic_counts.items():
        if ns == "industries":
            industry_fragments.append({"slug": slug, "count": count})
    industry_fragments.sort(key=lambda x: -x["count"])

    clients_section = {
        "total": len(client_rows),
        "top": top_clients,
        "empty": empty_clients,
        "empty_count": len(empty_clients),
        "industry_fragments": industry_fragments,
    }

    # -----------------------------------------------------------------
    # SECTION 5: Freshness
    # -----------------------------------------------------------------
    earliest = today - timedelta(days=180)
    per_day: dict[str, int] = defaultdict(int)
    for frag in fragments:
        d = frag["date"]
        if d and len(d) >= 10:
            try:
                fd = datetime.strptime(d[:10], "%Y-%m-%d").date()
            except ValueError:
                continue
            if fd >= earliest:
                per_day[d[:10]] += 1

    heatmap_days = []
    cur = earliest
    while cur <= today:
        heatmap_days.append({
            "date": cur.strftime("%Y-%m-%d"),
            "count": per_day.get(cur.strftime("%Y-%m-%d"), 0),
            "weekday": cur.weekday(),
        })
        cur = cur + timedelta(days=1)

    # Stalest topics: oldest latest-date per topic
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

    # Most recent ingest per namespace
    recent_per_ns: dict[str, str] = {}
    for frag in fragments:
        ns = frag["namespace"]
        d = frag["date"]
        if d and d > recent_per_ns.get(ns, ""):
            recent_per_ns[ns] = d

    freshness = {
        "heatmap": heatmap_days,
        "heatmap_max": max((d["count"] for d in heatmap_days), default=1),
        "stalest_topics": stalest_list,
        "recent_per_namespace": recent_per_ns,
    }

    # -----------------------------------------------------------------
    # SECTION 6: Session Notes & Other Buckets
    # -----------------------------------------------------------------
    session_notes = {
        "session_notes_count": by_namespace.get("session-notes", 0),
        "concepts_count": by_namespace.get("concepts", 0),
        "other_count": by_namespace.get("other", 0),
        "other_breakdown": {
            bucket: sum(
                1 for f in (WIKI_DIR / bucket).rglob("*.md")
                if f.name not in ("_index.md", "README.md", "PLACEHOLDER.md")
            ) if (WIKI_DIR / bucket).exists() else 0
            for bucket in misc_namespaces
        },
    }

    return {
        "total_fragments": total_fragments,
        "overview": overview,
        "synthesis": synthesis,
        "density": density,
        "clients": clients_section,
        "freshness": freshness,
        "session_notes": session_notes,
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

    _validate_csrf()
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
    filepath = _safe_resolve(article_path)
    if filepath is None:
        return "Not found", 404
    name = make_download_name(filepath, article_path)
    return send_file(filepath, as_attachment=True, download_name=name,
                     mimetype="text/markdown")


@app.route("/download/pdf/<path:article_path>")
def download_pdf(article_path):
    """Download article as PDF."""
    filepath = _safe_resolve(article_path)
    if filepath is None:
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
