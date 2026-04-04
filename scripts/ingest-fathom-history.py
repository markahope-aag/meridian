#!/usr/bin/env python3
"""Bulk-ingest past Fathom meetings into Meridian capture/.

Usage:
    python scripts/ingest-fathom-history.py --dry-run --limit 20
    python scripts/ingest-fathom-history.py --after 2026-01-01
    python scripts/ingest-fathom-history.py --limit 5

Requires env vars:
    FATHOM_API_KEY          — Fathom API bearer token
    MERIDIAN_RECEIVER_URL   — e.g. https://meridian.markahope.com
    MERIDIAN_RECEIVER_TOKEN — receiver bearer token
"""

import argparse
import os
import sys
import time

import requests


FATHOM_API = "https://api.fathom.ai/external/v1"


def get_fathom_headers():
    key = os.environ.get("FATHOM_API_KEY")
    if not key:
        print("Error: FATHOM_API_KEY env var not set", file=sys.stderr)
        sys.exit(1)
    return {"Authorization": f"Bearer {key}"}


def get_receiver_config():
    url = os.environ.get("MERIDIAN_RECEIVER_URL")
    token = os.environ.get("MERIDIAN_RECEIVER_TOKEN")
    if not url or not token:
        # Try ~/.meridian/config.yaml
        try:
            from pathlib import Path
            import yaml
            config_path = Path.home() / ".meridian" / "config.yaml"
            with open(config_path) as f:
                config = yaml.safe_load(f)
            url = url or config.get("receiver_url")
            token = token or config.get("token")
        except Exception:
            pass
    if not url or not token:
        print("Error: MERIDIAN_RECEIVER_URL and MERIDIAN_RECEIVER_TOKEN required", file=sys.stderr)
        print("       Set env vars or configure ~/.meridian/config.yaml", file=sys.stderr)
        sys.exit(1)
    return url.rstrip("/"), token


def fetch_meetings(headers, after=None, before=None, limit=None):
    """Fetch meetings from Fathom API with pagination."""
    meetings = []
    cursor = None

    while True:
        params = {}
        if cursor:
            params["cursor"] = cursor
        if after:
            params["after"] = after
        if before:
            params["before"] = before

        resp = requests.get(
            f"{FATHOM_API}/calls",
            headers=headers,
            params=params,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        calls = data.get("calls", data.get("data", []))
        if isinstance(data, list):
            calls = data

        meetings.extend(calls)
        print(f"  Fetched {len(calls)} meetings (total: {len(meetings)})", file=sys.stderr)

        if limit and len(meetings) >= limit:
            meetings = meetings[:limit]
            break

        cursor = data.get("next_cursor") or data.get("cursor")
        if not cursor or not calls:
            break

        # Rate limit: ~1 req/sec stays well under 60/min
        time.sleep(1)

    return meetings


def fetch_meeting_detail(meeting_id, headers):
    """Fetch full meeting details including transcript, summary, action items."""
    resp = requests.get(
        f"{FATHOM_API}/calls/{meeting_id}",
        headers=headers,
        params={
            "include_transcript": "true",
            "include_summary": "true",
            "include_action_items": "true",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def send_to_receiver(meeting, receiver_url, receiver_token):
    """POST meeting to meridian receiver /capture/fathom."""
    resp = requests.post(
        f"{receiver_url}/capture/fathom",
        headers={
            "Authorization": f"Bearer {receiver_token}",
            "Content-Type": "application/json",
        },
        json=meeting,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def main():
    parser = argparse.ArgumentParser(description="Bulk-ingest Fathom meetings into Meridian")
    parser.add_argument("--limit", type=int, help="Max meetings to ingest")
    parser.add_argument("--after", help="Only meetings after this date (YYYY-MM-DD)")
    parser.add_argument("--before", help="Only meetings before this date (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true", help="List meetings without ingesting")
    args = parser.parse_args()

    fathom_headers = get_fathom_headers()
    receiver_url, receiver_token = get_receiver_config()

    print("Fetching meetings from Fathom...", file=sys.stderr)
    meetings = fetch_meetings(fathom_headers, after=args.after, before=args.before, limit=args.limit)

    if not meetings:
        print("No meetings found.", file=sys.stderr)
        return

    print(f"\nFound {len(meetings)} meetings.\n", file=sys.stderr)

    ingested = 0
    skipped = 0
    failed = 0

    for i, meeting in enumerate(meetings, 1):
        title = meeting.get("title") or meeting.get("meeting_title") or "Untitled"
        meeting_id = meeting.get("id") or meeting.get("recording_id") or "?"
        created = meeting.get("created_at") or meeting.get("scheduled_start_time") or "?"
        date_str = created[:10] if len(str(created)) >= 10 else created

        if args.dry_run:
            print(f"  [{i}/{len(meetings)}] {date_str} — {title} (id: {meeting_id})")
            continue

        print(f"  [{i}/{len(meetings)}] {date_str} — {title}...", end=" ", file=sys.stderr)

        try:
            # Fetch full details with transcript
            detail = fetch_meeting_detail(meeting_id, fathom_headers)
            time.sleep(1)  # rate limit

            result = send_to_receiver(detail, receiver_url, receiver_token)
            print(f"✓ {result.get('filename', 'ok')}", file=sys.stderr)
            ingested += 1
            time.sleep(0.5)  # gentle on the receiver
        except Exception as e:
            print(f"✗ {e}", file=sys.stderr)
            failed += 1

    print(f"\n{'=' * 40}", file=sys.stderr)
    if args.dry_run:
        print(f"Dry run: {len(meetings)} meetings would be ingested.", file=sys.stderr)
    else:
        print(f"Ingested: {ingested}", file=sys.stderr)
        print(f"Failed:   {failed}", file=sys.stderr)
        print(f"Total:    {ingested + failed}", file=sys.stderr)


if __name__ == "__main__":
    main()
