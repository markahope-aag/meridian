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

# `synthesis_queue.json` currently holds two unrelated record shapes:
#   1. L3 topic items (this scheduler's own work) — keyed by `topic`.
#   2. L4 candidate proposals from `conceptual_agent.py` Mode C — keyed by
#      `signal`, with `type == "layer4_candidate"`. These have no `topic`
#      field and are NOT meant to be consumed here. Mode A is the
#      intended consumer but does not currently read them back.
# The helpers below filter L4 records out so this scheduler only sees its
# own work. Moving L4 candidates into their own file would be the cleaner
# fix; this is a defensive shim until that refactor happens.
def _is_topic_item(item: dict) -> bool:
    return isinstance(item, dict) and item.get("type") != "layer4_candidate" and "topic" in item


def load_queue() -> list[dict]:
    """Load the synthesis queue from JSON file."""
    if not QUEUE_PATH.exists():
        return []
    with open(QUEUE_PATH) as f:
        return json.load(f)


def load_topic_items() -> list[dict]:
    """Load only L3 topic items from the queue, skipping foreign records."""
    return [i for i in load_queue() if _is_topic_item(i)]


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
    """Get current queue status.

    Counts only L3 topic items (the records this scheduler actually
    processes). L4 candidate proposals are counted separately under
    `layer4_candidates_pending` so the dashboard can still see them
    without conflating them with topic work.
    """
    raw = load_queue()
    topic_items = [i for i in raw if _is_topic_item(i)]
    layer4 = [i for i in raw if isinstance(i, dict) and i.get("type") == "layer4_candidate"]

    status = {"pending": 0, "running": 0, "complete": 0, "failed": 0,
              "total": len(topic_items), "next_5": [],
              "layer4_candidates_pending": sum(
                  1 for i in layer4 if i.get("status") == "pending"
              )}

    for item in topic_items:
        s = item.get("status", "pending")
        if s in status:
            status[s] += 1

    pending = [i for i in topic_items if i.get("status") == "pending"]
    pending.sort(key=lambda x: x.get("priority", 0), reverse=True)
    status["next_5"] = [
        {"topic": i["topic"], "fragment_count": i.get("fragment_count", 0)}
        for i in pending[:5]
    ]
    return status


# evolution_detector writes its `dimension` field using wiki-namespace
# names ("knowledge", "industries", "engineering"); synthesizer.py uses
# its own short labels ("topic", "industry", "engineering"). Map between
# them so an evolution-queued item flows straight through.
_DIMENSION_MAP = {
    "knowledge": "topic",
    "industries": "industry",
    "engineering": "engineering",
    "interests": "interests",
    "clients": "clients",
}


def _priority_key(item: dict) -> float:
    """Tolerant priority sort. Existing populate items use int priorities
    (0-100); evolution-queued items use string "high"/"medium"/"low".
    Comparing mixed types would crash — coerce everything to float."""
    p = item.get("priority", 0)
    if isinstance(p, (int, float)):
        return float(p)
    if isinstance(p, str):
        return {"high": 1000.0, "medium": 500.0, "low": 100.0}.get(p.lower(), 0.0)
    return 0.0


def _matches_item(q: dict, topic: str, dimension: str) -> bool:
    """Match a queue entry by (topic, dimension). Legacy populate items
    have no dimension field — treat them as topic-dimension."""
    if q.get("topic") != topic:
        return False
    q_dim = q.get("dimension")
    if q_dim is None:
        return dimension == "topic"
    return _DIMENSION_MAP.get(q_dim, q_dim) == dimension


def _drift_files_for(article_dim: str, slug: str) -> list:
    """Layer 4 drift reports written by evolution_detector for this
    article. Filename pattern: <dimension>-<slug>-<date>.md where
    dimension is the wiki namespace ('knowledge', 'industries', etc.)"""
    drift_dir = ROOT / "wiki" / "layer4" / "drift"
    if not drift_dir.exists():
        return []
    return sorted(drift_dir.glob(f"{article_dim}-{slug}-*.md"))


def process_pending(limit: int = 5, force: bool = False, queued_by: str | None = None):
    """Process the next N pending topics.

    When `queued_by` is set, only consider items where queued_by matches
    AND pass force=True to synthesize_topic (these items already have
    Layer 3 articles — re-synthesis is the whole point).

    When `queued_by` is None (the default daily run), exclude items
    that have a queued_by marker. Those are owned by a separate cron
    and processing them here without force would just mark them
    "complete" without re-synthesizing.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location("synthesizer", ROOT / "agents" / "synthesizer.py")
    synth_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(synth_mod)
    synthesize_topic = synth_mod.synthesize_topic

    queue = load_queue()
    pending = [i for i in queue if i.get("status") == "pending" and _is_topic_item(i)]
    if queued_by:
        pending = [i for i in pending if i.get("queued_by") == queued_by]
        effective_force = True  # evolution-queued items always need force
    else:
        pending = [i for i in pending if not i.get("queued_by")]
        effective_force = force
    pending.sort(key=_priority_key, reverse=True)
    to_process = pending[:limit]

    if not to_process:
        print("No pending topics in queue.", file=sys.stderr)
        return []

    results = []
    for item in to_process:
        topic = item["topic"]
        # Synthesizer dimension. Evolution items store the wiki namespace
        # ("knowledge"/"industries"/"engineering"); legacy populate items
        # omit dimension and default to "topic".
        raw_dim = item.get("dimension")
        synth_dim = _DIMENSION_MAP.get(raw_dim, raw_dim) if raw_dim else "topic"
        now = datetime.now(timezone.utc).isoformat()

        for q in queue:
            if _matches_item(q, topic, synth_dim):
                q["status"] = "running"
                q["started_at"] = now
        save_queue(queue)

        print(f"\nSynthesizing: {synth_dim}/{topic}", file=sys.stderr)
        try:
            result = synthesize_topic(topic, force=effective_force, dimension=synth_dim)

            for q in queue:
                if _matches_item(q, topic, synth_dim):
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

            # When re-synthesis from an evolution-queued item succeeds,
            # the drift reports that triggered the queue are now stale —
            # the evidence behind them has been incorporated.
            if queued_by == "evolution_detector" and "error" not in result and raw_dim:
                for drift in _drift_files_for(raw_dim, topic):
                    try:
                        drift.unlink()
                    except OSError:
                        pass

            results.append(result)

        except Exception as e:
            print(f"  Error: {e}", file=sys.stderr)
            for q in queue:
                if _matches_item(q, topic, synth_dim):
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
    parser.add_argument("--force", action="store_true", help="Overwrite existing Layer 3 articles")
    parser.add_argument("--queued-by", default=None,
                        help="Process only items queued by this source (e.g. 'evolution_detector'). "
                             "When set, force is implied and queue_by-tagged items are the only ones "
                             "considered. Default daily runs exclude such items.")
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
    results = process_pending(args.limit, force=args.force, queued_by=args.queued_by)
    output = {
        "status": "ok",
        "processed": len(results),
        "results": results,
    }
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
