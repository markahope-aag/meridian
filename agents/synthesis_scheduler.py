#!/usr/bin/env python3
"""Meridian Synthesis Scheduler — process pending topics from the synthesis queue.

Reads synthesis_queue from Supabase, synthesizes the next N pending topics.

Usage:
    python agents/synthesis_scheduler.py              # process next 5 pending
    python agents/synthesis_scheduler.py --limit 10   # process next 10
    python agents/synthesis_scheduler.py --populate   # populate queue from topics.yaml
    python agents/synthesis_scheduler.py --status     # show queue status

Requires env vars:
    SUPABASE_URL
    SUPABASE_SERVICE_ROLE_KEY
    ANTHROPIC_API_KEY
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml


ROOT = Path(__file__).parent.parent
WIKI_DIR = ROOT / "wiki"

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://mpktcabncjodpmyfqeht.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")


def supabase_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def supabase_get(table: str, params: str = "") -> list:
    resp = requests.get(
        f"{SUPABASE_URL}/rest/v1/{table}?{params}",
        headers=supabase_headers(), timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def supabase_post(table: str, data: list) -> list:
    resp = requests.post(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers={**supabase_headers(), "Prefer": "return=representation,resolution=merge-duplicates"},
        json=data, timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def supabase_patch(table: str, match: dict, data: dict):
    params = "&".join(f"{k}=eq.{v}" for k, v in match.items())
    resp = requests.patch(
        f"{SUPABASE_URL}/rest/v1/{table}?{params}",
        headers=supabase_headers(),
        json=data, timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def create_table_if_needed():
    """Create synthesis_queue table via Supabase SQL if it doesn't exist."""
    # Try a read first
    try:
        supabase_get("synthesis_queue", "limit=1")
        return True
    except Exception:
        pass

    # Table doesn't exist — create via SQL
    sql = """
    CREATE TABLE IF NOT EXISTS synthesis_queue (
        id SERIAL PRIMARY KEY,
        topic TEXT UNIQUE NOT NULL,
        topic_path TEXT NOT NULL,
        fragment_count INTEGER DEFAULT 0,
        priority INTEGER DEFAULT 50,
        status TEXT DEFAULT 'pending',
        started_at TIMESTAMPTZ,
        completed_at TIMESTAMPTZ,
        error TEXT,
        evidence_count INTEGER,
        confidence TEXT,
        created_at TIMESTAMPTZ DEFAULT NOW()
    );
    """
    resp = requests.post(
        f"{SUPABASE_URL}/rest/v1/rpc/exec_sql",
        headers=supabase_headers(),
        json={"query": sql}, timeout=30,
    )
    # If RPC doesn't exist, user needs to create table manually
    if resp.status_code != 200:
        print("Cannot auto-create table. Create it manually in Supabase SQL editor:", file=sys.stderr)
        print(sql, file=sys.stderr)
        return False
    return True


def populate_queue():
    """Populate synthesis_queue with all topics from topics.yaml."""
    topics_path = ROOT / "topics.yaml"
    with open(topics_path) as f:
        data = yaml.safe_load(f) or {}

    # Get fragment counts from filesystem
    knowledge_dir = WIKI_DIR / "knowledge"
    rows = []
    priority = 100

    for item in data.get("categories", []):
        slug = item.get("slug", "")
        name = item.get("name", slug)
        topic_dir = knowledge_dir / slug
        fragment_count = 0
        if topic_dir.exists():
            fragment_count = sum(1 for f in topic_dir.rglob("*.md")
                                if f.name not in ("_index.md", "index.md"))

        rows.append({
            "topic": slug,
            "topic_path": f"wiki/knowledge/{slug}",
            "fragment_count": fragment_count,
            "priority": priority,
            "status": "pending",
        })
        priority -= 1

    # Sort by fragment count descending for priority
    rows.sort(key=lambda x: x["fragment_count"], reverse=True)
    for i, row in enumerate(rows):
        row["priority"] = 100 - i

    # Upsert
    result = supabase_post("synthesis_queue", rows)
    print(f"Populated {len(rows)} topics in synthesis_queue", file=sys.stderr)
    return rows


def get_queue_status() -> dict:
    """Get current queue status."""
    all_items = supabase_get("synthesis_queue", "order=priority.desc")
    status = {"pending": 0, "running": 0, "complete": 0, "failed": 0, "next_5": []}

    for item in all_items:
        s = item.get("status", "pending")
        if s in status:
            status[s] += 1

    pending = [i for i in all_items if i.get("status") == "pending"]
    status["next_5"] = [
        {"topic": i["topic"], "fragment_count": i.get("fragment_count", 0)}
        for i in pending[:5]
    ]
    status["total"] = len(all_items)
    return status


def process_pending(limit: int = 5):
    """Process the next N pending topics."""
    from agents.synthesizer import synthesize_topic

    pending = supabase_get(
        "synthesis_queue",
        f"status=eq.pending&order=priority.desc&limit={limit}"
    )

    if not pending:
        print("No pending topics in queue.", file=sys.stderr)
        return []

    results = []
    for item in pending:
        topic = item["topic"]
        now = datetime.now(timezone.utc).isoformat()

        # Mark as running
        supabase_patch("synthesis_queue", {"topic": topic},
                       {"status": "running", "started_at": now})

        print(f"\nSynthesizing: {topic}", file=sys.stderr)
        try:
            result = synthesize_topic(topic)

            if "error" in result:
                supabase_patch("synthesis_queue", {"topic": topic},
                               {"status": "failed", "error": result["error"],
                                "completed_at": datetime.now(timezone.utc).isoformat()})
            else:
                supabase_patch("synthesis_queue", {"topic": topic}, {
                    "status": "complete",
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                    "evidence_count": result.get("evidence_count", 0),
                    "confidence": "established" if result.get("evidence_count", 0) >= 10
                                  else "high" if result.get("evidence_count", 0) >= 5
                                  else "medium",
                })
            results.append(result)

        except Exception as e:
            print(f"  Error: {e}", file=sys.stderr)
            supabase_patch("synthesis_queue", {"topic": topic},
                           {"status": "failed", "error": str(e),
                            "completed_at": datetime.now(timezone.utc).isoformat()})
            results.append({"topic": topic, "error": str(e)})

    return results


def main():
    parser = argparse.ArgumentParser(description="Meridian Synthesis Scheduler")
    parser.add_argument("--populate", action="store_true", help="Populate queue from topics.yaml")
    parser.add_argument("--status", action="store_true", help="Show queue status")
    parser.add_argument("--limit", type=int, default=5, help="Max topics to process")
    args = parser.parse_args()

    if not SUPABASE_KEY:
        print("Error: SUPABASE_SERVICE_ROLE_KEY env var not set", file=sys.stderr)
        sys.exit(1)

    if args.populate:
        create_table_if_needed()
        rows = populate_queue()
        print(json.dumps({"status": "ok", "populated": len(rows)}, indent=2))
        return

    if args.status:
        status = get_queue_status()
        print(json.dumps(status, indent=2))
        return

    # Process pending
    results = process_pending(args.limit)
    output = {
        "status": "ok",
        "processed": len(results),
        "results": results,
    }
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
