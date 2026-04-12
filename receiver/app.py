#!/usr/bin/env python3
"""Meridian Receiver — central API for the Meridian knowledge system.

All writes to the Meridian filesystem and all agent execution go through this service.
Deployed on Coolify with a bind mount to /meridian/.

Endpoints:
    POST /capture              — generic markdown capture
    POST /capture/fathom       — Fathom meeting webhook
    POST /capture/claude-session — Claude Code session transcript
    POST /capture/gdrive       — ingest a Google Drive file (from Sieve)
    GET  /check                — check if a gdrive file already exists
    POST /ask                  — Q&A against the wiki
    POST /debrief              — debrief a Claude Code session
    POST /context              — search wiki for a context brief
    GET  /health               — health check
"""

import hashlib
import io
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from flask import Flask, Response, jsonify, request
import yaml

app = Flask(__name__)
log = logging.getLogger("meridian")

# ---------------------------------------------------------------------------
# Capture size cap
# ---------------------------------------------------------------------------
#
# Meridian is a downstream sink: Sieve handles human review and should not be
# forwarding giant files. Cap incoming capture payloads so corrupted or
# pathologically large documents are rejected at the boundary with a clear
# JSON error, rather than wedging the distill queue.
#
# Limits apply to:
#   - /capture (generic)       : the `content` field
#   - /capture/gdrive          : the markdown extracted from the drive file
#   - /capture/fathom          : the transcript body
#   - /capture/claude-session  : the session transcript
#
# The Flask-level MAX_CONTENT_LENGTH is a hard upper bound on the raw request
# body; it catches truly absurd uploads before JSON parsing. Per-route checks
# enforce the tighter text-size cap and return a clean envelope.

MAX_CAPTURE_BYTES = 1_000_000  # 1 MB of text content per capture document
MAX_REQUEST_BYTES = 2 * 1024 * 1024  # 2 MB of raw request body (JSON overhead)
app.config["MAX_CONTENT_LENGTH"] = MAX_REQUEST_BYTES


@app.errorhandler(413)
def _too_large(_err):
    """Return a JSON envelope instead of Flask's default HTML 413."""
    return (
        jsonify(
            {
                "status": "error",
                "error": "payload_too_large",
                "message": (
                    f"Capture payload exceeds the {MAX_REQUEST_BYTES} byte request limit "
                    f"or the {MAX_CAPTURE_BYTES} byte content limit. "
                    "Shrink or split the document in Sieve before resending."
                ),
                "max_request_bytes": MAX_REQUEST_BYTES,
                "max_capture_bytes": MAX_CAPTURE_BYTES,
            }
        ),
        413,
    )


def _enforce_capture_size(text: str, source: str) -> tuple[dict, int] | None:
    """Return a 413 JSON response if `text` exceeds the capture size cap.

    Returns None when the text is within bounds.
    """
    size = len(text.encode("utf-8"))
    if size <= MAX_CAPTURE_BYTES:
        return None
    log.warning(
        "Rejecting oversized capture from %s: %d bytes (cap=%d)",
        source,
        size,
        MAX_CAPTURE_BYTES,
    )
    return (
        {
            "status": "error",
            "error": "payload_too_large",
            "message": (
                f"Capture content is {size} bytes; cap is {MAX_CAPTURE_BYTES}. "
                "Shrink or split the document in Sieve before resending."
            ),
            "source": source,
            "size_bytes": size,
            "max_capture_bytes": MAX_CAPTURE_BYTES,
        },
        413,
    )

# ---------------------------------------------------------------------------
# Async job tracking — SQLite-backed so state survives gunicorn worker
# restarts and is visible across the two workers. Replaces the previous
# per-worker in-memory dict, which caused "job not found" on polls that
# landed on a different worker than the one that created the job.
# ---------------------------------------------------------------------------

JOBS_DB_PATH = Path(
    os.environ.get("MERIDIAN_JOBS_DB", "/meridian/state/jobs.db")
)
_jobs_db_lock = threading.Lock()  # guards schema init; SQLite handles concurrent writes


def _jobs_conn() -> sqlite3.Connection:
    """Open a per-call SQLite connection.

    SQLite connections are not safe to share across gunicorn worker
    processes, so each call opens its own. WAL mode lets multiple
    workers read and write concurrently without blocking each other.
    """
    JOBS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(JOBS_DB_PATH), timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def _init_jobs_db() -> None:
    """Create the jobs table if it doesn't exist. Idempotent."""
    with _jobs_db_lock:
        with _jobs_conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id           TEXT PRIMARY KEY,
                    type         TEXT NOT NULL,
                    status       TEXT NOT NULL,
                    started_at   TEXT NOT NULL,
                    completed_at TEXT,
                    result       TEXT,
                    error        TEXT
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);")


_init_jobs_db()


def create_job(job_type: str) -> str:
    """Create a new background job, return its ID."""
    job_id = uuid.uuid4().hex[:12]
    with _jobs_conn() as conn:
        conn.execute(
            "INSERT INTO jobs (id, type, status, started_at) VALUES (?, ?, 'running', ?)",
            (job_id, job_type, datetime.now(timezone.utc).isoformat()),
        )
    return job_id


def complete_job(job_id: str, result: str) -> None:
    """Mark a job as completed with its result."""
    with _jobs_conn() as conn:
        conn.execute(
            "UPDATE jobs SET status='completed', completed_at=?, result=? WHERE id=?",
            (datetime.now(timezone.utc).isoformat(), result, job_id),
        )


def fail_job(job_id: str, error: str) -> None:
    """Mark a job as failed."""
    with _jobs_conn() as conn:
        conn.execute(
            "UPDATE jobs SET status='failed', completed_at=?, error=? WHERE id=?",
            (datetime.now(timezone.utc).isoformat(), error, job_id),
        )


def get_job(job_id: str) -> dict | None:
    """Get job status by id, or None if not found."""
    with _jobs_conn() as conn:
        row = conn.execute(
            "SELECT id, type, status, started_at, completed_at, result, error "
            "FROM jobs WHERE id=?",
            (job_id,),
        ).fetchone()
    return dict(row) if row else None


def run_agent_async(job_id: str, args: list[str]):
    """Run an agent subprocess in a background thread."""
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=600,
            cwd=str(MERIDIAN_ROOT),
        )
        if result.returncode != 0:
            fail_job(job_id, result.stderr)
        else:
            complete_job(job_id, result.stdout)
    except subprocess.TimeoutExpired:
        fail_job(job_id, "Agent timed out (600s)")
    except FileNotFoundError as e:
        fail_job(job_id, f"Agent not found: {e}")
    except Exception as e:
        fail_job(job_id, str(e))

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

    oversized = _enforce_capture_size(content, source=f"/capture ({source_type})")
    if oversized:
        body, status = oversized
        return jsonify(body), status

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

    # Dedup: check if this recording_id already exists in capture/ or raw/
    rid_str = str(recording_id)
    for search_dir in (CAPTURE_DIR, RAW_DIR):
        if search_dir.exists():
            for f in search_dir.glob(f"*-{rid_str}.md"):
                return jsonify({
                    "status": "skipped",
                    "reason": "duplicate",
                    "recording_id": recording_id,
                    "existing_file": str(f),
                })

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

    oversized = _enforce_capture_size(md, source=f"/capture/fathom ({recording_id})")
    if oversized:
        body, status = oversized
        body["recording_id"] = recording_id
        return jsonify(body), status

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

    oversized = _enforce_capture_size(md, source=f"/capture/claude-session ({project_name})")
    if oversized:
        body, status = oversized
        body["project"] = project_name
        return jsonify(body), status

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
# POST /distill — run Daily Distill agent (async by default)
# ---------------------------------------------------------------------------

@app.route("/distill", methods=["POST"])
@require_auth
def distill():
    """Run the Daily Distill agent to review capture/ and promote to raw/.

    Returns 202 with a job ID. Poll GET /jobs/<id> for results.
    Add ?sync=true for synchronous execution (blocks until complete).

    Body JSON:
        mode: str (optional) — "auto" for automatic, "dry-run" for scoring only
        file: str (optional) — specific capture file to process
    """
    data = request.get_json(force=True)
    mode = data.get("mode", "auto")
    file_arg = data.get("file")
    sync = request.args.get("sync", "").lower() == "true"

    args = [sys.executable, str(AGENTS_DIR / "daily_distill.py")]
    if mode == "dry-run":
        args.append("--dry-run")
    if file_arg:
        args.extend(["--file", file_arg])

    if sync:
        try:
            result = subprocess.run(
                args, capture_output=True, text=True, timeout=300,
                cwd=str(MERIDIAN_ROOT),
            )
            if result.returncode != 0:
                return jsonify({"error": "daily_distill failed", "stderr": result.stderr}), 500
            return jsonify({"status": "ok", "result": result.stdout})
        except subprocess.TimeoutExpired:
            return jsonify({"error": "daily_distill timed out"}), 504
        except FileNotFoundError:
            return jsonify({"error": "daily_distill.py not found"}), 501

    job_id = create_job("distill")
    thread = threading.Thread(target=run_agent_async, args=(job_id, args), daemon=True)
    thread.start()
    return jsonify({"status": "accepted", "job_id": job_id}), 202


# ---------------------------------------------------------------------------
# POST /compile — run the compiler agent (async by default)
# ---------------------------------------------------------------------------

@app.route("/compile", methods=["POST"])
@require_auth
def compile():
    """Run the compiler agent to compile raw/ documents into wiki/ articles.

    Returns 202 with a job ID. Poll GET /jobs/<id> for results.
    Add ?sync=true for synchronous execution (blocks until complete).

    Body JSON:
        file: str (optional) — specific raw file to compile; if omitted, compiles all uncompiled
    """
    data = request.get_json(force=True)
    file_arg = data.get("file")
    sync = request.args.get("sync", "").lower() == "true"

    args = [sys.executable, str(AGENTS_DIR / "compiler.py")]
    if file_arg:
        args.extend(["--file", file_arg])

    if sync:
        try:
            result = subprocess.run(
                args, capture_output=True, text=True, timeout=600,
                cwd=str(MERIDIAN_ROOT),
            )
            if result.returncode != 0:
                return jsonify({"error": "compiler failed", "stderr": result.stderr}), 500
            return jsonify({"status": "ok", "result": result.stdout})
        except subprocess.TimeoutExpired:
            return jsonify({"error": "compiler timed out"}), 504
        except FileNotFoundError:
            return jsonify({"error": "compiler.py not found"}), 501

    job_id = create_job("compile")
    thread = threading.Thread(target=run_agent_async, args=(job_id, args), daemon=True)
    thread.start()
    return jsonify({"status": "accepted", "job_id": job_id}), 202


# ---------------------------------------------------------------------------
# POST /lint — run the linter agent (async by default)
# ---------------------------------------------------------------------------

@app.route("/lint", methods=["POST"])
@require_auth
def lint():
    """Run the linter agent to check wiki health.

    Returns 202 with a job ID. Poll GET /jobs/<id> for results.
    Add ?sync=true for synchronous execution.

    Body JSON:
        mode: str (optional) — "auto" (default) or "dry-run"
        scope: str (optional) — "contradictions", "orphans", "gaps", or "all" (default)
    """
    data = request.get_json(force=True)
    mode = data.get("mode", "auto")
    scope = data.get("scope", "all")
    sync = request.args.get("sync", "").lower() == "true"

    args = [sys.executable, str(AGENTS_DIR / "linter.py"), "--scope", scope]
    if mode == "dry-run":
        args.append("--dry-run")

    if sync:
        try:
            result = subprocess.run(
                args, capture_output=True, text=True, timeout=600,
                cwd=str(MERIDIAN_ROOT),
            )
            if result.returncode != 0:
                return jsonify({"error": "linter failed", "stderr": result.stderr}), 500
            return jsonify({"status": "ok", "result": result.stdout})
        except subprocess.TimeoutExpired:
            return jsonify({"error": "linter timed out"}), 504
        except FileNotFoundError:
            return jsonify({"error": "linter.py not found"}), 501

    job_id = create_job("lint")
    thread = threading.Thread(target=run_agent_async, args=(job_id, args), daemon=True)
    thread.start()
    return jsonify({"status": "accepted", "job_id": job_id}), 202


# ---------------------------------------------------------------------------
# POST /watchdog — run the watchdog to detect and fix stuck items
# ---------------------------------------------------------------------------

@app.route("/watchdog", methods=["POST"])
@require_auth
def watchdog():
    """Run the pipeline watchdog.

    Body JSON:
        dry_run: bool (optional) — report only, no actions
    """
    data = request.get_json(force=True) if request.data else {}
    dry_run = data.get("dry_run", False)

    args = [sys.executable, str(AGENTS_DIR / "watchdog.py")]
    if dry_run:
        args.append("--dry-run")

    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=120,
            cwd=str(MERIDIAN_ROOT),
        )
        if result.returncode != 0:
            return jsonify({"error": "watchdog failed", "stderr": result.stderr}), 500
        return jsonify({"status": "ok", "result": result.stdout})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# POST /synthesize — synthesize a single topic
# ---------------------------------------------------------------------------

@app.route("/synthesize", methods=["POST"])
@require_auth
def synthesize():
    """Synthesize a Layer 3 article for a topic.

    Body JSON:
        topic: str (required) — canonical topic slug
    """
    data = request.get_json(force=True)
    topic = data.get("topic")
    if not topic:
        return jsonify({"error": "topic is required"}), 400

    args = [sys.executable, str(AGENTS_DIR / "synthesizer.py"), "--topic", topic]

    job_id = create_job("synthesize")
    thread = threading.Thread(target=run_agent_async, args=(job_id, args), daemon=True)
    thread.start()
    return jsonify({"status": "accepted", "job_id": job_id}), 202


# ---------------------------------------------------------------------------
# POST /synthesize/schedule — process next N pending topics
# ---------------------------------------------------------------------------

@app.route("/synthesize/schedule", methods=["POST"])
@require_auth
def synthesize_schedule():
    """Process next pending topics from the synthesis queue."""
    data = request.get_json(force=True) if request.data else {}
    limit = data.get("limit", 5)

    args = [sys.executable, str(AGENTS_DIR / "synthesis_scheduler.py"),
            "--limit", str(limit)]

    job_id = create_job("synthesize_schedule")
    thread = threading.Thread(target=run_agent_async, args=(job_id, args), daemon=True)
    thread.start()
    return jsonify({"status": "accepted", "job_id": job_id}), 202


# ---------------------------------------------------------------------------
# GET /synthesize/queue — synthesis queue status
# ---------------------------------------------------------------------------

@app.route("/synthesize/queue", methods=["GET"])
def synthesize_queue():
    """Get synthesis queue status. No auth required."""
    queue_path = MERIDIAN_ROOT / "synthesis_queue.json"
    if not queue_path.exists():
        return jsonify({"pending": 0, "running": 0, "complete": 0, "failed": 0,
                        "total": 0, "next_5": []})

    try:
        with open(queue_path) as f:
            items = json.load(f)

        status = {"pending": 0, "running": 0, "complete": 0, "failed": 0, "total": len(items)}
        for item in items:
            s = item.get("status", "pending")
            if s in status:
                status[s] += 1

        pending = [i for i in items if i.get("status") == "pending"]
        pending.sort(key=lambda x: x.get("priority", 0), reverse=True)
        status["next_5"] = [
            {"topic": i["topic"], "fragment_count": i.get("fragment_count", 0)}
            for i in pending[:5]
        ]

        return jsonify(status)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# POST /conceptualize — run the Layer 4 conceptual agent
# ---------------------------------------------------------------------------

@app.route("/conceptualize", methods=["POST"])
@require_auth
def conceptualize():
    """Run the conceptual agent in one of four modes.

    Body JSON:
        mode: "connections" | "maturation" | "emergence" | "contradictions" (required)
        dry_run: bool (optional) — default false
        limit: int (optional) — for Mode A, cap the number of articles written
        verbose: bool (optional) — default false

    Returns 202 with a job_id. Poll GET /jobs/<id> for the result.
    Add ?sync=true for synchronous execution (blocks up to 10 minutes).
    """
    data = request.get_json(force=True) if request.data else {}
    mode = data.get("mode")
    if mode not in ("connections", "maturation", "emergence", "contradictions"):
        return jsonify({"error": "mode must be one of: connections, maturation, emergence, contradictions"}), 400

    dry_run = bool(data.get("dry_run", False))
    limit = data.get("limit")
    verbose = bool(data.get("verbose", False))
    sync = request.args.get("sync", "").lower() == "true"

    args = [
        sys.executable,
        str(AGENTS_DIR / "conceptual_agent.py"),
        "--mode", mode,
    ]
    if dry_run:
        args.append("--dry-run")
    if verbose:
        args.append("--verbose")
    if limit is not None:
        try:
            args.extend(["--limit", str(int(limit))])
        except (TypeError, ValueError):
            return jsonify({"error": "limit must be an integer"}), 400

    if sync:
        try:
            result = subprocess.run(
                args, capture_output=True, text=True, timeout=600,
                cwd=str(MERIDIAN_ROOT),
            )
            if result.returncode != 0:
                return jsonify({
                    "error": "conceptual agent failed",
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                }), 500
            return jsonify({"status": "ok", "result": result.stdout})
        except subprocess.TimeoutExpired:
            return jsonify({"error": "conceptual agent timed out (10 min cap)"}), 504
        except FileNotFoundError:
            return jsonify({"error": "conceptual_agent.py not found"}), 501

    job_id = create_job(f"conceptualize-{mode}")
    thread = threading.Thread(target=run_agent_async, args=(job_id, args), daemon=True)
    thread.start()
    return jsonify({"status": "accepted", "job_id": job_id, "mode": mode}), 202


# ---------------------------------------------------------------------------
# GET /jobs/<id> — poll job status
# ---------------------------------------------------------------------------

@app.route("/jobs/<job_id>", methods=["GET"])
@require_auth
def job_status(job_id):
    """Poll the status of an async job.

    Returns:
        status: "running" | "completed" | "failed"
        result: agent output (when completed)
        error: error message (when failed)
    """
    job = get_job(job_id)
    if not job:
        return jsonify({"error": "job not found"}), 404
    return jsonify(job)


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
# Google Drive helpers (for /capture/gdrive)
# ---------------------------------------------------------------------------

_gdrive_service = None


def get_gdrive_service():
    """Build a Google Drive API client from the GOOGLE_SERVICE_ACCOUNT_JSON env var."""
    global _gdrive_service
    if _gdrive_service is not None:
        return _gdrive_service

    sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if not sa_json:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON env var not set")

    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    info = json.loads(sa_json)
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/drive.readonly"]
    )
    _gdrive_service = build("drive", "v3", credentials=creds)
    return _gdrive_service


# Mime types exportable as plain text
_EXPORT_TYPES = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.presentation": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
}


def download_gdrive_text(file_id: str, mime_type: str) -> str:
    """Download a Google Drive file and return its text content."""
    service = get_gdrive_service()

    # Google Workspace files: export as text
    if mime_type in _EXPORT_TYPES:
        export_mime = _EXPORT_TYPES[mime_type]
        resp = service.files().export(fileId=file_id, mimeType=export_mime).execute()
        text = resp.decode("utf-8") if isinstance(resp, bytes) else resp
        return text.strip()

    # PDF: download and extract with pypdf
    if mime_type == "application/pdf":
        resp = service.files().get_media(fileId=file_id).execute()
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(resp))
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n\n".join(pages).strip()

    # DOCX: download and extract with python-docx
    if mime_type in (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ):
        resp = service.files().get_media(fileId=file_id).execute()
        from docx import Document
        doc = Document(io.BytesIO(resp))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        return "\n\n".join(paragraphs).strip()

    # Default: try downloading as plain text
    resp = service.files().get_media(fileId=file_id).execute()
    text = resp.decode("utf-8", errors="replace") if isinstance(resp, bytes) else str(resp)
    return text.strip()


# ---------------------------------------------------------------------------
# POST /capture/gdrive — ingest a Google Drive file from Sieve
# ---------------------------------------------------------------------------

@app.route("/capture/gdrive", methods=["POST"])
@require_auth
def capture_gdrive():
    """Download a file from Google Drive, convert to markdown, write to capture/.

    Body JSON:
        file_id: str (required) — Google Drive file ID
        filename: str (required)
        drive_url: str
        mime_type: str
        metadata: {folder, modified_at, owner_email, word_count}
    """
    data = request.get_json(force=True)
    file_id = data.get("file_id")
    filename = data.get("filename")
    mime_type = data.get("mime_type", "")
    drive_url = data.get("drive_url", "")
    metadata = data.get("metadata", {})

    if not file_id or not filename:
        return jsonify({"status": "error", "error": "file_id and filename are required"}), 400

    # Dedup: check if this gdrive_file_id already exists
    existing = find_gdrive_file(file_id)
    if existing:
        return jsonify({
            "status": "skipped",
            "reason": "duplicate",
            "file": existing,
        })

    # Download and convert
    try:
        text = download_gdrive_text(file_id, mime_type)
    except Exception as e:
        log.error("Failed to download gdrive file %s: %s", file_id, e)
        return jsonify({"status": "error", "error": f"download failed: {e}"}), 500

    if not text:
        return jsonify({"status": "error", "error": "no text content extracted"}), 422

    oversized = _enforce_capture_size(text, source=f"/capture/gdrive ({filename})")
    if oversized:
        body, status = oversized
        body["file_id"] = file_id
        body["filename"] = filename
        return jsonify(body), status

    # Strip extension from title
    title = Path(filename).stem

    # Build frontmatter
    frontmatter = yaml.dump({
        "title": title,
        "source_url": drive_url,
        "source_type": "gdrive",
        "gdrive_file_id": file_id,
        "gdrive_folder": metadata.get("folder", ""),
        "date_ingested": now_ts(),
        "compiled_at": "",
        "owner": metadata.get("owner_email", ""),
        "modified_at": metadata.get("modified_at", ""),
        "word_count": metadata.get("word_count"),
        "tags": [],
        "summary": "",
    }, default_flow_style=False, sort_keys=False).strip()

    md = f"---\n{frontmatter}\n---\n\n{text}\n"

    # Write to capture/
    date_prefix = now_str()
    safe_name = slugify(title)
    out_filename = f"{date_prefix}-{safe_name}.md"
    filepath = write_capture_file(out_filename, md)

    # Append to wiki/log.md
    append_log(f"ingest | gdrive: {filename}")

    relative_path = f"capture/{out_filename}"
    log.info("Captured gdrive file: %s -> %s", filename, relative_path)

    return jsonify({"status": "ok", "file": relative_path})


def append_log(entry: str):
    """Append an entry to wiki/log.md."""
    log_file = WIKI_DIR / "log.md"
    try:
        WIKI_DIR.mkdir(parents=True, exist_ok=True)
        date = now_str()
        line = f"\n## {date} {entry}\n"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception as e:
        log.warning("Failed to append to log.md: %s", e)


# ---------------------------------------------------------------------------
# GET /check — check if a gdrive file already exists in Meridian
# ---------------------------------------------------------------------------

@app.route("/check", methods=["GET"])
@require_auth
def check_gdrive():
    """Check if a file with the given gdrive_file_id already exists.

    Query params:
        gdrive_file_id: str (required)

    Response:
        {"exists": false}
        {"exists": true, "location": "capture|raw|wiki", "file": "path/to/file.md"}
    """
    gdrive_file_id = request.args.get("gdrive_file_id")
    if not gdrive_file_id:
        return jsonify({"error": "gdrive_file_id query param required"}), 400

    result = find_gdrive_file(gdrive_file_id)
    if result:
        # Determine location from path
        location = "capture"
        if "/raw/" in result or result.startswith("raw/"):
            location = "raw"
        elif "/wiki/" in result or result.startswith("wiki/"):
            location = "wiki"
        return jsonify({"exists": True, "location": location, "file": result})

    return jsonify({"exists": False})


def find_gdrive_file(gdrive_file_id: str) -> str | None:
    """Scan capture/, raw/, wiki/ for a file with matching gdrive_file_id in frontmatter."""
    for search_dir in (CAPTURE_DIR, RAW_DIR, WIKI_DIR):
        if not search_dir.exists():
            continue
        for md_file in search_dir.rglob("*.md"):
            try:
                head = md_file.read_text(encoding="utf-8", errors="replace")[:2000]
                if f"gdrive_file_id: {gdrive_file_id}" in head:
                    return str(md_file.relative_to(MERIDIAN_ROOT))
            except Exception:
                continue
    return None


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
