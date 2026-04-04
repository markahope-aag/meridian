#!/usr/bin/env python3
"""Meridian Receiver — central API for the Meridian knowledge system.

All writes to the Meridian filesystem and all agent execution go through this service.
Deployed on Coolify with a bind mount to /meridian/.

Endpoints:
    POST /capture              — generic markdown capture
    POST /capture/fathom       — Fathom meeting webhook
    POST /capture/claude-session — Claude Code session transcript
    POST /ask                  — Q&A against the wiki
    POST /debrief              — debrief a Claude Code session
    POST /context              — search wiki for a context brief
    GET  /health               — health check
"""

import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from flask import Flask, Response, jsonify, request
import yaml

app = Flask(__name__)
log = logging.getLogger("meridian")

# Paths — set via env or default to /meridian
MERIDIAN_ROOT = Path(os.environ.get("MERIDIAN_ROOT", "/meridian"))
CAPTURE_DIR = MERIDIAN_ROOT / "capture"
RAW_DIR = MERIDIAN_ROOT / "raw"
WIKI_DIR = MERIDIAN_ROOT / "wiki"
OUTPUTS_DIR = MERIDIAN_ROOT / "outputs"
AGENTS_DIR = MERIDIAN_ROOT / "agents"
PROMPTS_DIR = MERIDIAN_ROOT / "prompts"


def get_token():
    return os.environ.get("MERIDIAN_RECEIVER_TOKEN", "")


def require_auth(f):
    """Require Bearer token authentication."""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        token = get_token()
        if not token:
            return jsonify({"error": "MERIDIAN_RECEIVER_TOKEN not configured"}), 500
        if auth != f"Bearer {token}":
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated


def slugify(text: str) -> str:
    """Convert text to kebab-case filename slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text[:80].strip("-")


def now_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def now_ts():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_capture_file(filename: str, content: str) -> Path:
    """Write a file to capture/ and return the path."""
    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    filepath = CAPTURE_DIR / filename
    filepath.write_text(content, encoding="utf-8")
    return filepath


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "meridian_root": str(MERIDIAN_ROOT),
        "capture_exists": CAPTURE_DIR.exists(),
        "wiki_exists": WIKI_DIR.exists(),
    })


# ---------------------------------------------------------------------------
# POST /capture — generic markdown capture
# ---------------------------------------------------------------------------

@app.route("/capture", methods=["POST"])
@require_auth
def capture_generic():
    """Accept markdown content and write to capture/.

    Body JSON:
        title: str (required)
        content: str (required) — markdown body
        source_url: str (optional)
        source_type: str (optional, default "note")
        tags: list[str] (optional)
    """
    data = request.get_json(force=True)
    title = data.get("title")
    content = data.get("content")

    if not title or not content:
        return jsonify({"error": "title and content are required"}), 400

    source_url = data.get("source_url", "")
    source_type = data.get("source_type", "note")
    tags = data.get("tags", [])

    frontmatter = yaml.dump({
        "title": title,
        "source_url": source_url,
        "source_type": source_type,
        "date_captured": now_str(),
        "tags": tags,
    }, default_flow_style=False, sort_keys=False).strip()

    md = f"---\n{frontmatter}\n---\n\n{content}\n"

    content_hash = hashlib.md5(content.encode()).hexdigest()[:6]
    filename = f"{now_str()}-{slugify(title)}-{content_hash}.md"
    filepath = write_capture_file(filename, md)

    return jsonify({"status": "ok", "file": str(filepath), "filename": filename})


# ---------------------------------------------------------------------------
# POST /capture/fathom — Fathom meeting webhook
# ---------------------------------------------------------------------------

@app.route("/capture/fathom", methods=["POST"])
@require_auth
def capture_fathom():
    """Format a Fathom new-meeting-content-ready webhook payload as .md.

    Expects the full Fathom Meeting object as JSON body.
    """
    data = request.get_json(force=True)

    recording_id = data.get("recording_id", "unknown")
    title = data.get("title") or data.get("meeting_title") or f"Meeting {recording_id}"
    url = data.get("url", "")
    share_url = data.get("share_url", "")
    created_at = data.get("created_at", now_ts())
    transcript = data.get("transcript") or []
    summary_obj = data.get("default_summary") or {}
    action_items = data.get("action_items") or []
    invitees = data.get("calendar_invitees") or []

    # Extract date from created_at
    meeting_date = created_at[:10] if len(created_at) >= 10 else now_str()

    # Build attendees list
    attendee_names = []
    for inv in invitees:
        name = inv.get("name") or inv.get("email") or "Unknown"
        email = inv.get("email", "")
        is_external = inv.get("is_external", False)
        label = f"{name} ({email})" if email else name
        if is_external:
            label += " [external]"
        attendee_names.append(label)

    # Build frontmatter
    tags = ["meeting"]
    frontmatter = yaml.dump({
        "title": title,
        "source_url": url,
        "source_type": "meeting",
        "date_captured": now_str(),
        "meeting_date": meeting_date,
        "recording_id": recording_id,
        "share_url": share_url,
        "tags": tags,
        "attendees": attendee_names,
    }, default_flow_style=False, sort_keys=False).strip()

    # Build markdown body
    sections = [f"---\n{frontmatter}\n---\n"]

    # Summary
    summary_text = summary_obj.get("markdown_formatted") or summary_obj.get("template_name") or ""
    if summary_text:
        sections.append(f"## Summary\n\n{summary_text}\n")

    # Attendees
    if attendee_names:
        att_list = "\n".join(f"- {a}" for a in attendee_names)
        sections.append(f"## Attendees\n\n{att_list}\n")

    # Action Items
    if action_items:
        items = []
        for item in action_items:
            desc = item.get("description", "")
            assignee = item.get("assignee") or {}
            assignee_name = assignee.get("name") or assignee.get("email") or ""
            completed = item.get("completed", False)
            checkbox = "[x]" if completed else "[ ]"
            line = f"- {checkbox} {desc}"
            if assignee_name:
                line += f" (@{assignee_name})"
            items.append(line)
        sections.append(f"## Action Items\n\n" + "\n".join(items) + "\n")

    # Full Transcript
    if transcript:
        lines = []
        for entry in transcript:
            speaker = entry.get("speaker", {})
            name = speaker.get("display_name", "Unknown")
            text = entry.get("text", "")
            timestamp = entry.get("timestamp", "")
            lines.append(f"**{name}** [{timestamp}]: {text}")
        sections.append(f"## Full Transcript\n\n" + "\n\n".join(lines) + "\n")

    md = "\n".join(sections)

    slug = slugify(title)
    filename = f"{meeting_date}-{slug}-{recording_id}.md"
    filepath = write_capture_file(filename, md)

    return jsonify({
        "status": "ok",
        "file": str(filepath),
        "filename": filename,
        "recording_id": recording_id,
    })


# ---------------------------------------------------------------------------
# POST /capture/claude-session — Claude Code session transcript
# ---------------------------------------------------------------------------

@app.route("/capture/claude-session", methods=["POST"])
@require_auth
def capture_claude_session():
    """Convert a Claude Code JSONL transcript to .md and write to capture/.

    Body JSON:
        transcript_path: str (required) — absolute path to the JSONL file on this machine
    """
    data = request.get_json(force=True)
    transcript_path = data.get("transcript_path")

    if not transcript_path:
        return jsonify({"error": "transcript_path is required"}), 400

    path = Path(transcript_path)
    if not path.exists():
        return jsonify({"error": f"file not found: {transcript_path}"}), 404

    # Parse the JSONL transcript
    lines = path.read_text(encoding="utf-8", errors="replace").strip().split("\n")
    messages = []
    session_id = None
    project_path = None

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        msg_type = entry.get("type", "")
        if msg_type == "system" or "session_id" in entry:
            session_id = session_id or entry.get("session_id") or entry.get("sessionId")
        if "cwd" in entry:
            project_path = project_path or entry.get("cwd")

        role = entry.get("role", "")
        content = entry.get("content", "")

        # Handle content that's a list of blocks
        if isinstance(content, list):
            text_parts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                    elif block.get("type") == "tool_use":
                        tool_name = block.get("name", "unknown")
                        tool_input = block.get("input", {})
                        text_parts.append(f"[Tool: {tool_name}]")
                    elif block.get("type") == "tool_result":
                        text_parts.append("[Tool result]")
                elif isinstance(block, str):
                    text_parts.append(block)
            content = "\n".join(text_parts)

        if role in ("user", "human") and content:
            messages.append({"role": "User", "content": content})
        elif role in ("assistant",) and content:
            messages.append({"role": "Claude", "content": content})

    # Derive project name from path
    project_name = "unknown-project"
    if project_path:
        project_name = Path(project_path).name
    elif transcript_path:
        # Try to decode from the path hash pattern (C--Users-markh-projects-foo)
        parts = Path(transcript_path).parts
        for part in parts:
            if part.startswith("C--") or part.startswith("c--"):
                decoded = part.replace("C--", "C:/").replace("c--", "c:/").replace("-", "/")
                project_name = decoded.split("/")[-1] if "/" in decoded else part
                break

    # Extract file paths mentioned
    file_paths = set()
    for msg in messages:
        paths_found = re.findall(r'[A-Za-z]?/?[\w./\\-]+\.\w{1,10}', msg["content"])
        for p in paths_found:
            if len(p) > 5 and not p.startswith("http"):
                file_paths.add(p)

    # Get session timestamp from filename or first entry
    session_date = now_str()
    try:
        # JSONL filenames are often timestamps
        stem = path.stem
        if len(stem) >= 10 and stem[:4].isdigit():
            session_date = stem[:10]
    except Exception:
        pass

    # Build frontmatter
    frontmatter = yaml.dump({
        "title": f"Claude Code Session — {project_name}",
        "source_url": "",
        "source_type": "claude-session",
        "date_captured": now_str(),
        "session_date": session_date,
        "session_id": session_id or path.stem,
        "project": project_name,
        "files_touched": sorted(file_paths)[:50],  # cap at 50
        "tags": ["claude-session", project_name],
    }, default_flow_style=False, sort_keys=False).strip()

    # Build markdown body
    sections = [f"---\n{frontmatter}\n---\n"]
    sections.append(f"# Claude Code Session — {project_name}\n")
    sections.append(f"**Date:** {session_date}  ")
    sections.append(f"**Project:** {project_name}  ")
    if session_id:
        sections.append(f"**Session ID:** {session_id}  ")
    sections.append("")

    if file_paths:
        sections.append("## Files Touched\n")
        for fp in sorted(file_paths)[:50]:
            sections.append(f"- `{fp}`")
        sections.append("")

    sections.append("## Transcript\n")
    for msg in messages:
        role = msg["role"]
        content = msg["content"].strip()
        if len(content) > 5000:
            content = content[:5000] + "\n\n[... truncated ...]"
        sections.append(f"### {role}\n\n{content}\n")

    md = "\n".join(sections)

    content_hash = hashlib.md5(md.encode()).hexdigest()[:6]
    filename = f"{session_date}-claude-session-{slugify(project_name)}-{content_hash}.md"
    filepath = write_capture_file(filename, md)

    return jsonify({
        "status": "ok",
        "file": str(filepath),
        "filename": filename,
        "project": project_name,
        "messages": len(messages),
    })


# ---------------------------------------------------------------------------
# POST /ask — Q&A against the wiki
# ---------------------------------------------------------------------------

@app.route("/ask", methods=["POST"])
@require_auth
def ask():
    """Accept a question, run Q&A agent against the wiki, return result.

    Body JSON:
        question: str (required)
    """
    data = request.get_json(force=True)
    question = data.get("question")

    if not question:
        return jsonify({"error": "question is required"}), 400

    try:
        result = subprocess.run(
            [sys.executable, str(AGENTS_DIR / "qa_agent.py"), "--question", question],
            capture_output=True, text=True, timeout=120,
            cwd=str(MERIDIAN_ROOT),
        )
        if result.returncode != 0:
            return jsonify({"error": "qa_agent failed", "stderr": result.stderr}), 500

        return jsonify({"status": "ok", "result": result.stdout})
    except subprocess.TimeoutExpired:
        return jsonify({"error": "qa_agent timed out"}), 504
    except FileNotFoundError:
        return jsonify({"error": "qa_agent.py not found — not yet implemented"}), 501


# ---------------------------------------------------------------------------
# POST /debrief — debrief a Claude Code session
# ---------------------------------------------------------------------------

@app.route("/debrief", methods=["POST"])
@require_auth
def debrief():
    """Run the debrief agent on a session transcript.

    Body JSON:
        session_id: str (optional) — specific session to debrief; if omitted, uses most recent
    """
    data = request.get_json(force=True)
    session_id = data.get("session_id")

    args = [sys.executable, str(AGENTS_DIR / "debrief.py")]
    if session_id:
        args.extend(["--session", session_id])

    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=120,
            cwd=str(MERIDIAN_ROOT),
        )
        if result.returncode != 0:
            return jsonify({"error": "debrief agent failed", "stderr": result.stderr}), 500

        return jsonify({"status": "ok", "result": result.stdout})
    except subprocess.TimeoutExpired:
        return jsonify({"error": "debrief agent timed out"}), 504
    except FileNotFoundError:
        return jsonify({"error": "debrief.py not found — not yet implemented"}), 501


# ---------------------------------------------------------------------------
# POST /context — wiki context brief
# ---------------------------------------------------------------------------

@app.route("/context", methods=["POST"])
@require_auth
def context():
    """Search the wiki for a topic and return a context brief.

    Body JSON:
        topic: str (required)
    """
    data = request.get_json(force=True)
    topic = data.get("topic")

    if not topic:
        return jsonify({"error": "topic is required"}), 400

    # Simple wiki search — find files mentioning the topic
    results = []
    if WIKI_DIR.exists():
        for md_file in WIKI_DIR.rglob("*.md"):
            try:
                content = md_file.read_text(encoding="utf-8", errors="replace")
                if topic.lower() in content.lower():
                    # Extract first 500 chars after frontmatter
                    body = content
                    if content.startswith("---"):
                        parts = content.split("---", 2)
                        if len(parts) >= 3:
                            body = parts[2].strip()
                    excerpt = body[:500]
                    results.append({
                        "file": str(md_file.relative_to(MERIDIAN_ROOT)),
                        "excerpt": excerpt,
                    })
            except Exception:
                continue

    if not results:
        return jsonify({
            "status": "ok",
            "topic": topic,
            "brief": f"No wiki articles found matching '{topic}'.",
            "results": [],
        })

    # Build a context brief
    brief_parts = [f"# Context Brief: {topic}\n"]
    for r in results[:10]:
        brief_parts.append(f"## {r['file']}\n\n{r['excerpt']}\n")

    return jsonify({
        "status": "ok",
        "topic": topic,
        "brief": "\n".join(brief_parts),
        "results": results[:10],
    })


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def check_directories():
    """Verify required directories exist and are writable on startup."""
    for name, path in [("capture", CAPTURE_DIR), ("raw", RAW_DIR)]:
        if not path.exists():
            log.warning("STARTUP: %s directory does not exist: %s", name, path)
        elif not os.access(path, os.W_OK):
            log.warning("STARTUP: %s directory is not writable: %s", name, path)
        else:
            log.info("STARTUP: %s directory OK: %s", name, path)


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
check_directories()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=True)
