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
from flask import Flask, jsonify, render_template, request
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


def convert_wikilinks(text: str) -> str:
    """Convert [[wikilinks]] to clickable HTML links."""
    def replace_link(match):
        full = match.group(1)
        if "|" in full:
            target, display = full.split("|", 1)
        else:
            target = full
            display = target.split("/")[-1].replace("-", " ").title()
        # Normalize path
        if not target.startswith("wiki/"):
            target = f"wiki/{target}"
        if not target.endswith(".md"):
            target += ".md"
        return f"[{display}](/article/{target})"
    return re.sub(r"\[\[([^\]]+)\]\]", replace_link, text)


def render_markdown(body: str) -> str:
    """Convert markdown body to HTML with wikilink support."""
    body = convert_wikilinks(body)
    md = get_md()
    return md.convert(body)

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

    # Count Layer 3 articles
    knowledge_dir = WIKI_DIR / "knowledge"
    if knowledge_dir.exists():
        for idx in knowledge_dir.rglob("index.md"):
            try:
                content = idx.read_text(encoding="utf-8", errors="replace")
                if "layer: 3" in content:
                    layer3_count += 1
            except Exception:
                pass

    return render_template("dashboard.html",
                           stats=stats, recent_log=recent_log,
                           clients=clients, topics=topics,
                           synth_status=synth_status, layer3_count=layer3_count)


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
    body_html = render_markdown(article["body"])

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


@app.route("/topic/<slug>")
def view_topic(slug):
    topic_dir = WIKI_DIR / "knowledge" / slug
    if not topic_dir.exists():
        return "Topic not found", 404

    # Check for Layer 3 synthesis
    synthesis = None
    synthesis_html = ""
    index_file = topic_dir / "index.md"
    if index_file.exists():
        synthesis = read_article(index_file)
        if synthesis.get("frontmatter", {}).get("layer") == 3:
            synthesis_html = render_markdown(synthesis["body"])
        else:
            synthesis = None

    # List Layer 2 articles
    articles = []
    for f in sorted(topic_dir.rglob("*.md")):
        if f.name in ("_index.md", "index.md"):
            continue
        articles.append(read_article(f))

    return render_template("topic.html", slug=slug, articles=articles,
                           synthesis=synthesis, synthesis_html=synthesis_html)


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


@app.route("/download/md/<path:article_path>")
def download_md(article_path):
    """Download article as raw markdown."""
    from flask import send_file
    filepath = MERIDIAN_ROOT / article_path
    if not filepath.exists() or not str(filepath).startswith(str(MERIDIAN_ROOT)):
        return "Not found", 404
    return send_file(filepath, as_attachment=True, download_name=filepath.name,
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
        import subprocess, tempfile
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w", encoding="utf-8") as tmp_html:
            tmp_html.write(html)
            tmp_html_path = tmp_html.name
        pdf_path = tmp_html_path.replace(".html", ".pdf")
        # Try wkhtmltopdf first, fall back to weasyprint
        try:
            subprocess.run(["wkhtmltopdf", "--quiet", tmp_html_path, pdf_path],
                           capture_output=True, timeout=30)
        except FileNotFoundError:
            try:
                from weasyprint import HTML
                HTML(string=html).write_pdf(pdf_path)
            except ImportError:
                # Last resort: return HTML as downloadable file
                import os
                os.unlink(tmp_html_path)
                from flask import Response
                return Response(html, mimetype="text/html",
                                headers={"Content-Disposition": f"attachment; filename={filepath.stem}.html"})
        from flask import send_file
        import os
        response = send_file(pdf_path, as_attachment=True,
                             download_name=f"{filepath.stem}.pdf", mimetype="application/pdf")
        os.unlink(tmp_html_path)
        # Clean up PDF after sending (deferred)
        return response
    except Exception as e:
        from flask import Response
        return Response(html, mimetype="text/html",
                        headers={"Content-Disposition": f"attachment; filename={filepath.stem}.html"})


@app.route("/api/stats")
def api_stats():
    return jsonify(get_stats())


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("WEB_PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=True)
