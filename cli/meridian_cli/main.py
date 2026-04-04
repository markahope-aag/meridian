#!/usr/bin/env python3
"""Meridian CLI — thin HTTP client for the Meridian knowledge system.

All commands make authenticated HTTP calls to the meridian-receiver on the VM.
Config is read from ~/.meridian/config.yaml.

Usage:
    meridian ask "What is the transformer architecture?"
    meridian debrief
    meridian debrief --session abc123
    meridian context "authentication"
    meridian capture --url https://example.com/article
    meridian capture --file ./notes.md
    meridian capture --text "Quick note about something"
    meridian status
"""

import argparse
import json
import sys
from pathlib import Path

import requests
import yaml


def load_config() -> dict:
    """Load config from ~/.meridian/config.yaml."""
    config_path = Path.home() / ".meridian" / "config.yaml"
    if not config_path.exists():
        print(
            "Error: ~/.meridian/config.yaml not found.\n"
            "Run scripts/setup-machine.sh to configure this machine,\n"
            "or create the file manually with receiver_url and token.",
            file=sys.stderr,
        )
        sys.exit(1)

    with open(config_path) as f:
        config = yaml.safe_load(f)

    if not config.get("receiver_url") or not config.get("token"):
        print("Error: receiver_url and token must be set in ~/.meridian/config.yaml", file=sys.stderr)
        sys.exit(1)

    return config


def api_call(method: str, endpoint: str, data: dict | None = None) -> dict:
    """Make an authenticated API call to the receiver."""
    config = load_config()
    url = f"{config['receiver_url'].rstrip('/')}{endpoint}"
    headers = {
        "Authorization": f"Bearer {config['token']}",
        "Content-Type": "application/json",
    }

    try:
        if method == "GET":
            resp = requests.get(url, headers=headers, timeout=130)
        else:
            resp = requests.post(url, headers=headers, json=data, timeout=130)
    except requests.ConnectionError:
        print(f"Error: cannot reach receiver at {config['receiver_url']}", file=sys.stderr)
        print("Is the meridian-receiver running on Coolify?", file=sys.stderr)
        sys.exit(1)
    except requests.Timeout:
        print("Error: request timed out (>130s)", file=sys.stderr)
        sys.exit(1)

    if resp.status_code == 401:
        print("Error: unauthorized — check your token in ~/.meridian/config.yaml", file=sys.stderr)
        sys.exit(1)

    try:
        return resp.json()
    except ValueError:
        return {"status": "error", "raw": resp.text}


def cmd_ask(args):
    """Ask the knowledge base a question."""
    question = " ".join(args.question)
    result = api_call("POST", "/ask", {"question": question})
    if result.get("status") == "ok":
        print(result.get("result", ""))
    else:
        print(f"Error: {result.get('error', 'unknown error')}", file=sys.stderr)
        sys.exit(1)


def cmd_debrief(args):
    """Debrief a Claude Code session."""
    data = {}
    if args.session:
        data["session_id"] = args.session

    result = api_call("POST", "/debrief", data)
    if result.get("status") == "ok":
        print(result.get("result", ""))
    else:
        print(f"Error: {result.get('error', 'unknown error')}", file=sys.stderr)
        sys.exit(1)


def cmd_context(args):
    """Get a context brief on a topic."""
    topic = " ".join(args.topic)
    result = api_call("POST", "/context", {"topic": topic})
    if result.get("status") == "ok":
        print(result.get("brief", ""))
    else:
        print(f"Error: {result.get('error', 'unknown error')}", file=sys.stderr)
        sys.exit(1)


def cmd_capture(args):
    """Capture content into the knowledge base."""
    if args.url:
        # Fetch URL content and send to receiver
        try:
            resp = requests.get(args.url, timeout=30, headers={
                "User-Agent": "Meridian/1.0"
            })
            resp.raise_for_status()
            content = resp.text
            title = args.title or args.url.split("/")[-1] or "untitled"
        except requests.RequestException as e:
            print(f"Error fetching URL: {e}", file=sys.stderr)
            sys.exit(1)

        result = api_call("POST", "/capture", {
            "title": title,
            "content": content,
            "source_url": args.url,
            "source_type": args.type or "article",
        })

    elif args.file:
        filepath = Path(args.file)
        if not filepath.exists():
            print(f"Error: file not found: {args.file}", file=sys.stderr)
            sys.exit(1)
        content = filepath.read_text(encoding="utf-8", errors="replace")
        title = args.title or filepath.stem.replace("-", " ").replace("_", " ").title()

        result = api_call("POST", "/capture", {
            "title": title,
            "content": content,
            "source_type": args.type or "note",
        })

    elif args.text:
        result = api_call("POST", "/capture", {
            "title": args.title or "Quick Note",
            "content": args.text,
            "source_type": args.type or "note",
        })
    else:
        print("Error: provide --url, --file, or --text", file=sys.stderr)
        sys.exit(1)

    if result.get("status") == "ok":
        print(f"Captured: {result.get('filename', 'unknown')}")
    else:
        print(f"Error: {result.get('error', 'unknown error')}", file=sys.stderr)
        sys.exit(1)


def cmd_status(args):
    """Check receiver health."""
    config = load_config()
    print(f"Receiver: {config['receiver_url']}")
    result = api_call("GET", "/health")
    if result.get("status") == "ok":
        print(f"Status:   healthy")
        print(f"Root:     {result.get('meridian_root', '?')}")
        print(f"Capture:  {'ok' if result.get('capture_exists') else 'missing'}")
        print(f"Wiki:     {'ok' if result.get('wiki_exists') else 'missing'}")
    else:
        print(f"Status:   unhealthy")
        print(f"Error:    {result.get('error', 'unknown')}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        prog="meridian",
        description="Meridian — personal knowledge system CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ask
    p_ask = sub.add_parser("ask", help="Ask the knowledge base a question")
    p_ask.add_argument("question", nargs="+", help="Your question")
    p_ask.set_defaults(func=cmd_ask)

    # debrief
    p_debrief = sub.add_parser("debrief", help="Debrief a Claude Code session")
    p_debrief.add_argument("--session", help="Specific session ID to debrief")
    p_debrief.set_defaults(func=cmd_debrief)

    # context
    p_context = sub.add_parser("context", help="Get a context brief on a topic")
    p_context.add_argument("topic", nargs="+", help="Topic to search for")
    p_context.set_defaults(func=cmd_context)

    # capture
    p_capture = sub.add_parser("capture", help="Capture content into the knowledge base")
    p_capture.add_argument("--url", help="URL to fetch and capture")
    p_capture.add_argument("--file", help="Local file to capture")
    p_capture.add_argument("--text", help="Raw text to capture")
    p_capture.add_argument("--title", help="Override title")
    p_capture.add_argument("--type", help="Source type override")
    p_capture.set_defaults(func=cmd_capture)

    # status
    p_status = sub.add_parser("status", help="Check receiver health")
    p_status.set_defaults(func=cmd_status)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
