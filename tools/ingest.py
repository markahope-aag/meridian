#!/usr/bin/env python3
"""Meridian Ingest — normalize a URL or file into capture/ as clean markdown.

Usage:
    python tools/ingest.py --url https://example.com/article
    python tools/ingest.py --file /path/to/document.pdf
    python tools/ingest.py --file /path/to/notes.md
    python tools/ingest.py --text "Some raw text content" --title "My Note"

Output: writes a .md file to capture/ with minimal frontmatter.
"""

import argparse
import hashlib
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml


def load_config():
    """Load config.yaml from project root."""
    config_path = Path(__file__).parent.parent / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def slugify(text: str) -> str:
    """Convert text to kebab-case filename slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text[:80].strip("-")


def fetch_url(url: str) -> dict:
    """Fetch content from a URL and return title + body."""
    resp = requests.get(url, timeout=30, headers={
        "User-Agent": "Meridian/1.0 (knowledge-system)"
    })
    resp.raise_for_status()
    content_type = resp.headers.get("content-type", "")

    if "text/html" in content_type:
        return extract_from_html(resp.text, url)
    else:
        # Plain text, markdown, etc.
        return {
            "title": url.split("/")[-1] or "untitled",
            "body": resp.text,
        }


def extract_from_html(html: str, url: str) -> dict:
    """Extract title and main text from HTML. Minimal extraction without BeautifulSoup."""
    # Extract title
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    title = title_match.group(1).strip() if title_match else "untitled"
    title = re.sub(r"\s+", " ", title)

    # Strip script and style tags
    body = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.IGNORECASE | re.DOTALL)
    # Strip HTML tags
    body = re.sub(r"<[^>]+>", "\n", body)
    # Collapse whitespace
    body = re.sub(r"\n{3,}", "\n\n", body)
    body = re.sub(r"[ \t]+", " ", body)
    body = body.strip()

    return {"title": title, "body": body}


def read_file(filepath: str) -> dict:
    """Read a local file and return title + body."""
    path = Path(filepath)
    if not path.exists():
        print(f"Error: file not found: {filepath}", file=sys.stderr)
        sys.exit(1)

    title = path.stem.replace("-", " ").replace("_", " ").title()
    body = path.read_text(encoding="utf-8", errors="replace")

    # If the file already has YAML frontmatter, preserve it in the body
    return {"title": title, "body": body}


def build_frontmatter(title: str, source_url: str = "", source_type: str = "note") -> str:
    """Build YAML frontmatter block."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    fm = {
        "title": title,
        "source_url": source_url,
        "source_type": source_type,
        "date_captured": now,
        "tags": [],
    }
    return "---\n" + yaml.dump(fm, default_flow_style=False, sort_keys=False).strip() + "\n---"


def write_capture(title: str, body: str, source_url: str, source_type: str,
                  config: dict) -> str:
    """Write normalized markdown to capture/ and return the filepath."""
    capture_dir = Path(__file__).parent.parent / config["paths"]["capture"]
    capture_dir.mkdir(parents=True, exist_ok=True)

    slug = slugify(title)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Add short hash to avoid collisions
    content_hash = hashlib.md5(body.encode()).hexdigest()[:6]
    filename = f"{date_str}-{slug}-{content_hash}.md"

    frontmatter = build_frontmatter(title, source_url, source_type)
    full_content = f"{frontmatter}\n\n{body}\n"

    filepath = capture_dir / filename
    filepath.write_text(full_content, encoding="utf-8")

    return str(filepath)


def detect_source_type(url: str = "", filepath: str = "") -> str:
    """Guess source_type from URL or file extension."""
    target = url or filepath
    target = target.lower()

    if any(x in target for x in ["arxiv.org", ".pdf"]):
        return "paper"
    if any(x in target for x in ["github.com", "gitlab.com"]):
        return "repo"
    if any(x in target for x in [".csv", ".json", ".parquet"]):
        return "dataset"
    if any(x in target for x in [".png", ".jpg", ".jpeg", ".gif", ".svg"]):
        return "image"
    if any(x in target for x in [".md", ".txt"]):
        return "note"
    return "article"


def main():
    parser = argparse.ArgumentParser(
        description="Meridian Ingest — normalize content into capture/"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--url", help="URL to fetch and ingest")
    group.add_argument("--file", help="Local file path to ingest")
    group.add_argument("--text", help="Raw text to ingest directly")

    parser.add_argument("--title", help="Override the document title")
    parser.add_argument("--source-type",
                        choices=["article", "paper", "repo", "dataset", "image", "note", "meeting"],
                        help="Override source type detection")

    args = parser.parse_args()
    config = load_config()

    if args.url:
        print(f"Fetching {args.url}...", file=sys.stderr)
        result = fetch_url(args.url)
        source_url = args.url
        auto_type = detect_source_type(url=args.url)
    elif args.file:
        print(f"Reading {args.file}...", file=sys.stderr)
        result = read_file(args.file)
        source_url = ""
        auto_type = detect_source_type(filepath=args.file)
    else:
        result = {
            "title": args.title or "untitled-note",
            "body": args.text,
        }
        source_url = ""
        auto_type = "note"

    title = args.title or result["title"]
    source_type = args.source_type or auto_type

    filepath = write_capture(
        title=title,
        body=result["body"],
        source_url=source_url,
        source_type=source_type,
        config=config,
    )

    # Output result as JSON to stdout (for n8n consumption)
    import json
    output = {
        "status": "ok",
        "file": filepath,
        "title": title,
        "source_type": source_type,
    }
    print(json.dumps(output))


if __name__ == "__main__":
    main()
