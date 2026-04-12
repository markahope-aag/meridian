#!/usr/bin/env python3
"""Sync raw documents from ClientBrain into Meridian's capture pipeline.

Pulls documents from ClientBrain's /api/meridian/export endpoint and
writes them to capture/clientbrain/<client-slug>/<source-type>-<id>.md
with appropriate frontmatter. The existing distill/compile/synthesize
pipeline picks them up from there.

Usage:
    python scripts/sync-clientbrain-documents.py
    python scripts/sync-clientbrain-documents.py --client adava-care
    python scripts/sync-clientbrain-documents.py --since 2026-04-01
    python scripts/sync-clientbrain-documents.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import requests
import yaml

SCRIPT_ROOT = Path(__file__).resolve().parent.parent
MERIDIAN_ROOT = Path(os.environ.get("MERIDIAN_ROOT", SCRIPT_ROOT))
CAPTURE_DIR = MERIDIAN_ROOT / "capture" / "clientbrain"
STATE_FILE = MERIDIAN_ROOT / "state" / "clientbrain-sync-state.json"

CLIENTBRAIN_URL = os.environ.get("CLIENTBRAIN_URL", "https://client-brain.vercel.app")
MERIDIAN_API_KEY = os.environ.get("MERIDIAN_CLIENTBRAIN_API_KEY", "")


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"last_sync": {}}


def save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def slugify(text: str) -> str:
    text = re.sub(r"[^a-z0-9\s-]", "", text.lower())
    return re.sub(r"\s+", "-", text).strip("-")[:60]


def fetch_documents(client_id: str | None, since: str | None, limit: int = 100) -> list[dict]:
    """Fetch documents from ClientBrain's export endpoint."""
    params: dict[str, str] = {"limit": str(limit)}
    if client_id:
        params["client_id"] = client_id
    if since:
        params["since"] = since

    all_docs: list[dict] = []
    offset = 0

    while True:
        params["offset"] = str(offset)
        resp = requests.get(
            f"{CLIENTBRAIN_URL}/api/meridian/export",
            headers={"Authorization": f"Bearer {MERIDIAN_API_KEY}"},
            params=params,
            timeout=30,
        )
        if resp.status_code != 200:
            print(f"ERROR: export returned {resp.status_code}: {resp.text[:200]}", file=sys.stderr)
            break
        data = resp.json()
        docs = data.get("documents", [])
        all_docs.extend(docs)
        if len(docs) < limit:
            break
        offset += limit

    return all_docs


def write_fragment(doc: dict, dry_run: bool) -> Path | None:
    """Write a ClientBrain document as a Meridian capture fragment."""
    client_name = doc.get("client_name") or "unknown"
    client_slug = slugify(client_name)
    source_type = doc.get("source_type", "unknown")
    source_id = doc.get("source_id", doc.get("id", ""))
    content = doc.get("content", "")
    created_at = doc.get("created_at", "")[:10]

    if not content.strip():
        return None

    # Map ClientBrain source types to Meridian source types
    type_map = {
        "email": "internal-email",
        "meeting": "internal-meeting",
        "slack": "internal-slack",
        "gdrive": "internal-drive",
        "clickup": "internal-clickup",
    }
    meridian_source_type = type_map.get(source_type, f"internal-{source_type}")

    out_dir = CAPTURE_DIR / client_slug
    # Filename: source_type-truncated_id.md
    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "", str(source_id))[:40]
    filename = f"{source_type}-{safe_id}.md"
    out_path = out_dir / filename

    if out_path.exists():
        return None  # skip duplicates

    # Build frontmatter
    title = content[:100].replace("\n", " ").strip()
    if len(title) > 80:
        title = title[:77] + "..."

    frontmatter = (
        f"---\n"
        f'title: "{title}"\n'
        f"layer: 1\n"
        f"source_type: {meridian_source_type}\n"
        f"source_origin: clientbrain\n"
        f"source_date: {created_at}\n"
        f'source_id: "{source_id}"\n'
        f'client_source: "{client_name}"\n'
        f"---\n\n"
    )

    if dry_run:
        return out_path

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_text(frontmatter + content, encoding="utf-8")
    return out_path


def main():
    parser = argparse.ArgumentParser(description="Sync ClientBrain documents to Meridian capture")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--client", help="Sync a specific client by name or slug")
    parser.add_argument("--since", help="Only docs since this date (YYYY-MM-DD)")
    parser.add_argument("--limit", type=int, default=100, help="Docs per page (default 100)")
    args = parser.parse_args()

    if not MERIDIAN_API_KEY:
        print("ERROR: MERIDIAN_CLIENTBRAIN_API_KEY env var not set", file=sys.stderr)
        sys.exit(1)

    state = load_state()
    since = args.since or state.get("last_sync", {}).get("all", "")

    print(f"Syncing from ClientBrain (since: {since or 'all time'})...", file=sys.stderr)

    docs = fetch_documents(
        client_id=None,  # TODO: resolve client name to UUID if --client specified
        since=since,
        limit=args.limit,
    )

    print(f"Fetched {len(docs)} documents", file=sys.stderr)

    written = 0
    skipped = 0
    for doc in docs:
        path = write_fragment(doc, args.dry_run)
        if path:
            written += 1
        else:
            skipped += 1

    print(json.dumps({
        "status": "ok",
        "dry_run": args.dry_run,
        "fetched": len(docs),
        "written": written,
        "skipped": skipped,
    }, indent=2))

    if not args.dry_run and docs:
        state["last_sync"]["all"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        save_state(state)


if __name__ == "__main__":
    main()
