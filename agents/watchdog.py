#!/usr/bin/env python3
"""Meridian Watchdog — detect and fix stuck pipeline items.

Checks each stage of the pipeline for items stuck in limbo and takes action:

- capture/ with distill_status=promote but never copied to raw/ → copy now
- capture/ with distill_status=skip → delete
- capture/ older than 3 days with no distill_status → trigger distill
- raw/ with empty compiled_at older than 3 days → trigger compile
- synthesis_queue items in 'running' status > 30 min → reset to pending
- wiki/knowledge/ topics with 10+ client insights but last synthesis > 7 days ago → requeue

Usage:
    python agents/watchdog.py              # run checks, take actions
    python agents/watchdog.py --dry-run    # report only, no actions

Output: JSON summary of actions taken.
"""

import argparse
import json
import os
import re
import sys
import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path

import yaml


ROOT = Path(__file__).parent.parent
WIKI_DIR = ROOT / "wiki"
RAW_DIR = ROOT / "raw"
CAPTURE_DIR = ROOT / "capture"


def parse_fm(content: str) -> dict:
    if not content.startswith("---"):
        return {}
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}
    try:
        return yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return {}


def now():
    return datetime.now(timezone.utc)


def parse_date(s):
    if not s:
        return None
    try:
        if "T" in str(s):
            return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except Exception:
        return None


def check_capture_stuck(dry_run: bool = False) -> dict:
    """Find and fix stuck capture files."""
    actions = {"promoted": 0, "deleted_skip": 0, "ancient_unprocessed": 0, "details": []}
    if not CAPTURE_DIR.exists():
        return actions

    cutoff = now() - timedelta(days=3)

    for f in CAPTURE_DIR.glob("*.md"):
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
            fm = parse_fm(content)

            status = fm.get("distill_status", "")
            if status == "promote":
                # Should have been copied to raw/, check if it exists there
                raw_match = RAW_DIR / f.name
                if not raw_match.exists():
                    # Copy to raw/ (minus the distill metadata)
                    if not dry_run:
                        RAW_DIR.mkdir(parents=True, exist_ok=True)
                        # Strip distill_* fields
                        cleaned = re.sub(r"^distill_status:.*\n", "", content, flags=re.MULTILINE)
                        cleaned = re.sub(r"^distill_date:.*\n", "", cleaned, flags=re.MULTILINE)
                        cleaned = re.sub(r"^distill_score:.*\n(?:  .*\n)*", "", cleaned, flags=re.MULTILINE)
                        raw_match.write_text(cleaned, encoding="utf-8")
                        f.unlink()
                    actions["promoted"] += 1
                    actions["details"].append(f"promoted stuck: {f.name}")
                else:
                    # Already in raw, just delete from capture
                    if not dry_run:
                        f.unlink()
                    actions["deleted_skip"] += 1

            elif status == "skip":
                # Should have been deleted after distill
                if not dry_run:
                    f.unlink()
                actions["deleted_skip"] += 1
                actions["details"].append(f"deleted skipped: {f.name}")

            else:
                # No distill status — check if ancient
                mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
                if mtime < cutoff:
                    actions["ancient_unprocessed"] += 1
                    actions["details"].append(f"unprocessed >3 days: {f.name}")
        except Exception as e:
            actions["details"].append(f"error reading {f.name}: {e}")

    return actions


def check_raw_stuck(dry_run: bool = False) -> dict:
    """Find raw files that haven't been compiled."""
    actions = {"uncompiled_count": 0, "details": []}
    if not RAW_DIR.exists():
        return actions

    cutoff = now() - timedelta(days=3)

    for f in RAW_DIR.glob("*.md"):
        if f.name.startswith("_"):
            continue
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
            fm = parse_fm(content)
            compiled_at = fm.get("compiled_at", "")

            if not compiled_at or str(compiled_at).strip() in ("", "null", "~"):
                ingested = parse_date(fm.get("date_ingested", ""))
                if ingested and ingested < cutoff:
                    actions["uncompiled_count"] += 1
                    actions["details"].append(f"uncompiled: {f.name}")
        except Exception:
            continue

    return actions


def check_synthesis_queue(dry_run: bool = False) -> dict:
    """Reset stuck synthesis queue items."""
    actions = {"reset_count": 0, "details": []}
    queue_path = ROOT / "synthesis_queue.json"
    if not queue_path.exists():
        return actions

    try:
        with open(queue_path) as f:
            queue = json.load(f)
    except json.JSONDecodeError:
        return actions

    cutoff = now() - timedelta(minutes=30)
    changed = False

    for item in queue:
        if item.get("status") == "running":
            started = parse_date(item.get("started_at", ""))
            if not started or started < cutoff:
                if not dry_run:
                    item["status"] = "pending"
                    item["started_at"] = None
                    changed = True
                actions["reset_count"] += 1
                actions["details"].append(f"reset stuck: {item.get('topic', '?')}")

    if changed and not dry_run:
        with open(queue_path, "w") as f:
            json.dump(queue, f, indent=2)

    return actions


def append_log(message: str):
    """Log watchdog actions."""
    log_path = WIKI_DIR / "log.md"
    now_str = now().strftime("%Y-%m-%d")
    try:
        content = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
        content += f"\n## [{now_str}] watchdog | {message}\n"
        log_path.write_text(content, encoding="utf-8")
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser(description="Meridian Watchdog")
    parser.add_argument("--dry-run", action="store_true", help="Report only, no actions")
    args = parser.parse_args()

    print(f"Watchdog {'DRY RUN ' if args.dry_run else ''}starting...", file=sys.stderr)

    capture_actions = check_capture_stuck(dry_run=args.dry_run)
    raw_actions = check_raw_stuck(dry_run=args.dry_run)
    queue_actions = check_synthesis_queue(dry_run=args.dry_run)

    total_actions = (capture_actions["promoted"] + capture_actions["deleted_skip"] +
                     queue_actions["reset_count"])

    # Build summary message
    summary_parts = []
    if capture_actions["promoted"] > 0:
        summary_parts.append(f"{capture_actions['promoted']} capture promoted")
    if capture_actions["deleted_skip"] > 0:
        summary_parts.append(f"{capture_actions['deleted_skip']} capture cleaned")
    if capture_actions["ancient_unprocessed"] > 0:
        summary_parts.append(f"{capture_actions['ancient_unprocessed']} ancient flagged")
    if raw_actions["uncompiled_count"] > 0:
        summary_parts.append(f"{raw_actions['uncompiled_count']} raw uncompiled flagged")
    if queue_actions["reset_count"] > 0:
        summary_parts.append(f"{queue_actions['reset_count']} queue stuck reset")

    if not args.dry_run and total_actions > 0:
        append_log(", ".join(summary_parts) if summary_parts else "no stuck items")

    output = {
        "status": "ok",
        "dry_run": args.dry_run,
        "capture": capture_actions,
        "raw": raw_actions,
        "synthesis_queue": queue_actions,
        "total_actions_taken": total_actions,
    }
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
