#!/usr/bin/env python3
"""Sync raw documents from ClientBrain into Meridian's capture pipeline.

Pulls documents from ClientBrain and writes them to
capture/clientbrain/<client-slug>/<source-type>-<id>.md with appropriate
frontmatter. The existing distill/compile/synthesize pipeline picks them
up from there.

Two modes:
  --direct   Pull straight from Supabase REST API (fast, for bulk/historical)
  (default)  Pull via ClientBrain's /api/meridian/export (for incremental)

Usage:
    python scripts/sync-clientbrain-documents.py
    python scripts/sync-clientbrain-documents.py --direct --source-types email
    python scripts/sync-clientbrain-documents.py --since 2026-04-01
    python scripts/sync-clientbrain-documents.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

SCRIPT_ROOT = Path(__file__).resolve().parent.parent
MERIDIAN_ROOT = Path(os.environ.get("MERIDIAN_ROOT", SCRIPT_ROOT))
CAPTURE_DIR = MERIDIAN_ROOT / "capture" / "clientbrain"
STATE_FILE = MERIDIAN_ROOT / "state" / "clientbrain-sync-state.json"

CLIENTBRAIN_URL = os.environ.get("CLIENTBRAIN_URL", "https://client-brain.vercel.app")
MERIDIAN_API_KEY = os.environ.get("MERIDIAN_CLIENTBRAIN_API_KEY", "")

# Direct Supabase access (for --direct mode)
SUPABASE_URL = os.environ.get("CLIENTBRAIN_SUPABASE_URL", "https://wxbgzeirqonwleljfowl.supabase.co")
SUPABASE_KEY = os.environ.get("CLIENTBRAIN_SUPABASE_KEY", "")

# Default source types — gdrive has 217K chunked spreadsheet rows, too noisy.
# meeting (Fathom) included: historical ones already exist in capture/external/
# but the since filter prevents re-import; new Fathoms flow through ClientBrain
# now that the direct n8n webhook is deactivated.
DEFAULT_SOURCE_TYPES = "email,meeting,slack,clickup"


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


# ---------------------------------------------------------------------------
# Fetch via Vercel API (incremental, small batches)
# ---------------------------------------------------------------------------

def fetch_via_api(client_id: str | None, since: str | None,
                  source_types: str | None = None, limit: int = 100) -> list[dict]:
    """Fetch documents via ClientBrain's Vercel export endpoint."""
    params: dict[str, str] = {"limit": str(limit)}
    if client_id:
        params["client_id"] = client_id
    if since:
        params["since"] = since
    if source_types:
        params["source_types"] = source_types

    all_docs: list[dict] = []
    offset = 0

    while True:
        params["offset"] = str(offset)
        resp = requests.get(
            f"{CLIENTBRAIN_URL}/api/meridian/export",
            headers={"Authorization": f"Bearer {MERIDIAN_API_KEY}"},
            params=params,
            timeout=60,
        )
        if resp.status_code != 200:
            print(f"ERROR: export returned {resp.status_code}: {resp.text[:200]}", file=sys.stderr)
            break
        data = resp.json()
        docs = data.get("documents", [])
        all_docs.extend(docs)
        print(f"  fetched {len(all_docs)} so far (page at offset {offset})...", file=sys.stderr)
        if len(docs) < limit:
            break
        offset += limit

    return all_docs


# ---------------------------------------------------------------------------
# Fetch directly from Supabase REST API (fast, for bulk pulls)
# ---------------------------------------------------------------------------

def _load_client_map() -> dict[str, str]:
    """Fetch client UUID → name mapping from Supabase."""
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
    }
    resp = requests.get(
        f"{SUPABASE_URL}/rest/v1/clients?select=id,name",
        headers=headers, timeout=30,
    )
    resp.raise_for_status()
    return {c["id"]: c["name"] for c in resp.json()}


def fetch_via_supabase(source_type: str, since: str | None,
                       client_map: dict[str, str],
                       page_size: int = 1000) -> list[dict]:
    """Pull one source_type directly from Supabase, page by page."""
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Prefer": "count=exact",
    }

    # Build base URL with filters — select only the columns we need (no embedding)
    base = (f"{SUPABASE_URL}/rest/v1/documents"
            f"?select=id,source_type,source_id,client_id,content,ingested_at,source_date,title"
            f"&source_type=eq.{source_type}"
            f"&order=ingested_at.asc")
    if since:
        base += f"&ingested_at=gte.{since}"

    # First request to get total count
    resp = requests.get(base + f"&limit=1", headers=headers, timeout=30)
    resp.raise_for_status()
    content_range = resp.headers.get("Content-Range", "*/0")
    total = int(content_range.split("/")[-1])
    print(f"  {source_type}: {total} documents to sync", file=sys.stderr)

    all_docs: list[dict] = []
    offset = 0

    while offset < total:
        resp = requests.get(
            base + f"&limit={page_size}&offset={offset}",
            headers=headers, timeout=120,
        )
        if resp.status_code not in (200, 206):
            print(f"  ERROR at offset {offset}: {resp.status_code} {resp.text[:200]}", file=sys.stderr)
            break

        rows = resp.json()
        for row in rows:
            all_docs.append({
                "id": row["id"],
                "source_type": row["source_type"],
                "source_id": row.get("source_id", ""),
                "client_id": row.get("client_id"),
                "client_name": client_map.get(row.get("client_id", ""), "unknown"),
                "content": row.get("content", ""),
                "created_at": row.get("ingested_at", ""),
                "source_date": row.get("source_date", ""),
            })

        offset += page_size
        if offset % 5000 == 0 or offset >= total:
            print(f"  {source_type}: {min(offset, total)}/{total} fetched...", file=sys.stderr)

        if len(rows) < page_size:
            break

    return all_docs


# ---------------------------------------------------------------------------
# Write capture fragment
# ---------------------------------------------------------------------------

TYPE_MAP = {
    "email": "internal-email",
    "meeting": "internal-meeting",
    "slack": "internal-slack",
    "gdrive": "internal-drive",
    "clickup": "internal-clickup",
}


def write_fragment(doc: dict, dry_run: bool) -> Path | None:
    """Write a ClientBrain document as a Meridian capture fragment."""
    client_name = doc.get("client_name") or "unknown"
    client_slug = slugify(client_name)
    source_type = doc.get("source_type", "unknown")
    source_id = doc.get("source_id", doc.get("id", ""))
    content = doc.get("content", "")
    created_at = (doc.get("created_at") or "")[:10]

    if not content.strip():
        return None

    meridian_source_type = TYPE_MAP.get(source_type, f"internal-{source_type}")

    out_dir = CAPTURE_DIR / client_slug
    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "", str(source_id))[:40]
    filename = f"{source_type}-{safe_id}.md"
    out_path = out_dir / filename

    if out_path.exists():
        return None  # skip duplicates

    # Build frontmatter — escape quotes in title
    title = content[:100].replace("\n", " ").strip()
    if len(title) > 80:
        title = title[:77] + "..."
    title = title.replace('"', '\\"')

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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Sync ClientBrain documents to Meridian capture")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--direct", action="store_true",
                        help="Pull directly from Supabase (fast, for bulk sync)")
    parser.add_argument("--client", help="Sync a specific client by name or slug")
    parser.add_argument("--since", help="Only docs since this date (YYYY-MM-DD)")
    parser.add_argument("--source-types", default=DEFAULT_SOURCE_TYPES,
                        help=f"Comma-separated source types (default: {DEFAULT_SOURCE_TYPES})")
    parser.add_argument("--limit", type=int, default=100,
                        help="Docs per page — API mode only (default 100)")
    args = parser.parse_args()

    if args.direct:
        if not SUPABASE_KEY:
            print("ERROR: CLIENTBRAIN_SUPABASE_KEY env var not set (needed for --direct)", file=sys.stderr)
            sys.exit(1)
    else:
        if not MERIDIAN_API_KEY:
            print("ERROR: MERIDIAN_CLIENTBRAIN_API_KEY env var not set", file=sys.stderr)
            sys.exit(1)

    state = load_state()
    since = args.since or state.get("last_sync", {}).get("all", "")
    types = [t.strip() for t in args.source_types.split(",")]

    print(f"Syncing from ClientBrain (since: {since or 'all time'}, "
          f"types: {','.join(types)}, mode: {'direct' if args.direct else 'api'})...",
          file=sys.stderr)

    if args.direct:
        client_map = _load_client_map()
        print(f"Loaded {len(client_map)} client mappings", file=sys.stderr)
        docs: list[dict] = []
        for st in types:
            docs.extend(fetch_via_supabase(st, since or None, client_map))
    else:
        docs = fetch_via_api(
            client_id=None,
            since=since,
            source_types=args.source_types,
            limit=args.limit,
        )

    print(f"Fetched {len(docs)} documents total", file=sys.stderr)

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
        "mode": "direct" if args.direct else "api",
        "fetched": len(docs),
        "written": written,
        "skipped": skipped,
    }, indent=2))

    if not args.dry_run and docs:
        state["last_sync"]["all"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        save_state(state)


if __name__ == "__main__":
    main()
