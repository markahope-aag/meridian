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

from flask import Flask, jsonify, render_template, request
import requests
import yaml

app = Flask(__name__)

MERIDIAN_ROOT = Path(os.environ.get("MERIDIAN_ROOT", "/meridian"))
WIKI_DIR = MERIDIAN_ROOT / "wiki"
RAW_DIR = MERIDIAN_ROOT / "raw"
CAPTURE_DIR = MERIDIAN_ROOT / "capture"
RECEIVER_URL = os.environ.get("MERIDIAN_RECEIVER_URL", "http://localhost:8000")
RECEIVER_TOKEN = os.environ.get("MERIDIAN_RECEIVER_TOKEN", "")


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
    """Get wiki statistics."""
    stats = {
        "wiki_total": 0,
        "articles": 0,
        "concepts": 0,
        "knowledge": 0,
        "clients_current": 0,
        "clients_former": 0,
        "raw": 0,
        "capture": 0,
        "knowledge_topics": 0,
        "client_folders": 0,
    }
    if WIKI_DIR.exists():
        stats["wiki_total"] = sum(1 for _ in WIKI_DIR.rglob("*.md"))
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
    if RAW_DIR.exists():
        stats["raw"] = sum(1 for _ in RAW_DIR.glob("*.md") if _.name != "_index.md")
    if CAPTURE_DIR.exists():
        stats["capture"] = sum(1 for _ in CAPTURE_DIR.glob("*.md"))
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

    # Client list
    clients = []
    clients_dir = WIKI_DIR / "clients" / "current"
    if clients_dir.exists():
        for d in sorted(clients_dir.iterdir()):
            if d.is_dir():
                article_count = sum(1 for _ in d.glob("*.md") if _.name != "_index.md")
                clients.append({"slug": d.name, "articles": article_count})

    # Knowledge topics
    topics = []
    knowledge_dir = WIKI_DIR / "knowledge"
    if knowledge_dir.exists():
        for d in sorted(knowledge_dir.iterdir()):
            if d.is_dir():
                article_count = sum(1 for _ in d.rglob("*.md") if _.name != "_index.md")
                topics.append({"slug": d.name, "articles": article_count})
        topics.sort(key=lambda x: x["articles"], reverse=True)

    return render_template("dashboard.html",
                           stats=stats, recent_log=recent_log,
                           clients=clients, topics=topics)


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


@app.route("/article/<path:article_path>")
def view_article(article_path):
    filepath = MERIDIAN_ROOT / article_path
    if not filepath.exists() or not str(filepath).startswith(str(MERIDIAN_ROOT)):
        return "Not found", 404

    article = read_article(filepath)

    # Convert markdown links to HTML links
    body_html = article["body"]
    # Convert [[wikilinks]] to clickable links
    body_html = re.sub(
        r"\[\[([^\]|]+?)(?:\|([^\]]+))?\]\]",
        lambda m: f'<a href="/article/wiki/{m.group(1)}.md">{m.group(2) or m.group(1)}</a>',
        body_html
    )

    return render_template("article.html", article=article, body_html=body_html)


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

    # List all articles
    articles = []
    for f in sorted(client_dir.glob("*.md")):
        if f.name == "_index.md":
            continue
        articles.append(read_article(f))

    return render_template("client.html", slug=slug,
                           index_article=index_article, articles=articles)


@app.route("/topic/<slug>")
def view_topic(slug):
    topic_dir = WIKI_DIR / "knowledge" / slug
    if not topic_dir.exists():
        return "Topic not found", 404

    articles = []
    for f in sorted(topic_dir.rglob("*.md")):
        if f.name == "_index.md":
            continue
        articles.append(read_article(f))

    return render_template("topic.html", slug=slug, articles=articles)


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
        answer = result.get("result", result.get("error", "No answer"))
    except Exception as e:
        answer = f"Error: {e}"

    return render_template("ask.html", question=question, answer=answer)


@app.route("/api/stats")
def api_stats():
    return jsonify(get_stats())


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("WEB_PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=True)
