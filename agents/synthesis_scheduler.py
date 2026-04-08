#!/usr/bin/env python3
"""Meridian Synthesis Scheduler — process pending topics from the synthesis queue.

Uses a JSON file at /meridian/synthesis_queue.json instead of a database.

Usage:
    python agents/synthesis_scheduler.py              # process next 5 pending
    python agents/synthesis_scheduler.py --limit 10   # process next 10
    python agents/synthesis_scheduler.py --populate   # populate queue from topics.yaml
    python agents/synthesis_scheduler.py --status     # show queue status
"""

import argparse
import json
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

import yaml


ROOT = Path(__file__).parent.parent
WIKI_DIR = ROOT / "wiki"
QUEUE_PATH = ROOT / "synthesis_queue.json"

_queue_lock = threading.Lock()


def load_queue() -> list[dict]:
    """Load the synthesis queue from JSON file."""
    if not QUEUE_PATH.exists():
        return []
    with open(QUEUE_PATH) as f:
        return json.load(f)


def save_queue(queue: list[dict]):
    """Save the synthesis queue to JSON file."""
    with _queue_lock:
        with open(QUEUE_PATH, "w") as f:
            json.dump(queue, f, indent=2)


def populate_queue():
    """Populate synthesis queue from topics.yaml and current fragment counts."""
    topics_path = ROOT / "topics.yaml"
    with open(topics_path) as f:
        data = yaml.safe_load(f) or {}

    knowledge_dir = WIKI_DIR / "knowledge"
    rows = []

    for item in data.get("categories", []):
        slug = item.get("slug", "")
        name = item.get("name", slug)
        topic_dir = knowledge_dir / slug
        fragment_count = 0
        if topic_dir.exists():
            fragment_count = sum(1 for f in topic_dir.rglob("*.md")
                                if f.name not in ("_index.md", "index.md"))

        # Check if already synthesized
        index_file = topic_dir / "index.md"
        already_done = False
        if index_file.exists():
            content = index_file.read_text(encoding="utf-8", errors="replace")
            if "layer: 3" in content:
                already_done = True

        rows.append({
            "topic": slug,
            "topic_name": name,
            "topic_path": f"wiki/knowledge/{slug}",
            "fragment_count": fragment_count,
            "status": "complete" if already_done else "pending",
            "started_at": None,
            "completed_at": None,
            "error": None,
            "evidence_count": None,
            "confidence": None,
        })

    # Sort by fragment count descending
    rows.sort(key=lambda x: x["fragment_count"], reverse=True)
    # Assign priority
    for i, row in enumerate(rows):
        row["priority"] = 100 - i

    save_queue(rows)
    print(f"Populated {len(rows)} topics in synthesis queue", file=sys.stderr)
    pending = sum(1 for r in rows if r["status"] == "pending")
    complete = sum(1 for r in rows if r["status"] == "complete")
    print(f"  Pending: {pending}, Already complete: {complete}", file=sys.stderr)
    return rows


def get_queue_status() -> dict:
    """Get current queue status."""
    queue = load_queue()
    status = {"pending": 0, "running": 0, "complete": 0, "failed": 0,
              "total": len(queue), "next_5": []}

    for item in queue:
        s = item.get("status", "pending")
        if s in status:
            status[s] += 1

    pending = [i for i in queue if i.get("status") == "pending"]
    pending.sort(key=lambda x: x.get("priority", 0), reverse=True)
    status["next_5"] = [
        {"topic": i["topic"], "fragment_count": i.get("fragment_count", 0)}
        for i in pending[:5]
    ]
    return status


def process_pending(limit: int = 5):
    """Process the next N pending topics."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("synthesizer", ROOT / "agents" / "synthesizer.py")
    synth_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(synth_mod)
    synthesize_topic = synth_mod.synthesize_topic

    queue = load_queue()
    pending = [i for i in queue if i.get("status") == "pending"]
    pending.sort(key=lambda x: x.get("priority", 0), reverse=True)
    to_process = pending[:limit]

    if not to_process:
        print("No pending topics in queue.", file=sys.stderr)
        return []

    results = []
    for item in to_process:
        topic = item["topic"]
        now = datetime.now(timezone.utc).isoformat()

        # Mark as running
        for q in queue:
            if q["topic"] == topic:
                q["status"] = "running"
                q["started_at"] = now
        save_queue(queue)

        print(f"\nSynthesizing: {topic}", file=sys.stderr)
        try:
            result = synthesize_topic(topic)

            for q in queue:
                if q["topic"] == topic:
                    if "error" in result:
                        q["status"] = "failed"
                        q["error"] = result["error"]
                    else:
                        q["status"] = "complete"
                        q["evidence_count"] = result.get("evidence_count", 0)
                        ec = result.get("evidence_count", 0)
                        q["confidence"] = ("established" if ec >= 10
                                           else "high" if ec >= 5
                                           else "medium" if ec >= 3
                                           else "low")
                    q["completed_at"] = datetime.now(timezone.utc).isoformat()
            save_queue(queue)
            results.append(result)

        except Exception as e:
            print(f"  Error: {e}", file=sys.stderr)
            for q in queue:
                if q["topic"] == topic:
                    q["status"] = "failed"
                    q["error"] = str(e)
                    q["completed_at"] = datetime.now(timezone.utc).isoformat()
            save_queue(queue)
            results.append({"topic": topic, "error": str(e)})

    return results


def main():
    parser = argparse.ArgumentParser(description="Meridian Synthesis Scheduler")
    parser.add_argument("--populate", action="store_true", help="Populate queue from topics.yaml")
    parser.add_argument("--status", action="store_true", help="Show queue status")
    parser.add_argument("--limit", type=int, default=5, help="Max topics to process")
    args = parser.parse_args()

    if args.populate:
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
