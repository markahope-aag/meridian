#!/usr/bin/env python3
"""Sync the canonical client registry from ClientBrain to Meridian.

Pulls the client list from ClientBrain's /api/clients endpoint and
regenerates clients.yaml so Meridian's compiler uses the same
canonical client identities as ClientBrain. New clients that appear
in ClickUp/Gmail/Slack flow into Meridian automatically.

Also pushes Meridian's topic and industry registries to ClientBrain
via POST /api/meridian/topics/sync and /api/meridian/industries/sync
so ClientBrain can tag documents with topic/industry metadata.

Usage:
    python scripts/sync-clientbrain-registry.py
    python scripts/sync-clientbrain-registry.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent

CLIENTBRAIN_URL = os.environ.get("CLIENTBRAIN_URL", "https://client-brain.vercel.app")
MERIDIAN_API_KEY = os.environ.get("MERIDIAN_CLIENTBRAIN_API_KEY", "")


def load_yaml(filename: str) -> dict:
    path = ROOT / filename
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def push_topics(dry_run: bool) -> dict:
    """Push topics.yaml to ClientBrain."""
    data = load_yaml("topics.yaml")
    entries = data.get("categories") or data.get("topics") or []
    topics = [
        {
            "slug": e["slug"],
            "display_name": e.get("name", e["slug"]),
            "meridian_confidence": None,
            "meridian_evidence_count": None,
        }
        for e in entries
        if isinstance(e, dict) and e.get("slug")
    ]
    if dry_run:
        print(f"Would push {len(topics)} topics to ClientBrain")
        return {"dry_run": True, "count": len(topics)}

    resp = requests.post(
        f"{CLIENTBRAIN_URL}/api/meridian/topics",
        headers={"Authorization": f"Bearer {MERIDIAN_API_KEY}"},
        json={"topics": topics},
        timeout=30,
    )
    result = resp.json()
    print(f"Topics sync: {result.get('upserted', 0)} upserted, {result.get('errors', 0)} errors")
    return result


def push_industries(dry_run: bool) -> dict:
    """Push industries.yaml to ClientBrain."""
    data = load_yaml("industries.yaml")
    entries = data.get("industries", [])
    industries = [
        {
            "slug": e["slug"],
            "display_name": e.get("name", e["slug"]),
        }
        for e in entries
        if isinstance(e, dict) and e.get("slug")
    ]
    if dry_run:
        print(f"Would push {len(industries)} industries to ClientBrain")
        return {"dry_run": True, "count": len(industries)}

    resp = requests.post(
        f"{CLIENTBRAIN_URL}/api/meridian/industries",
        headers={"Authorization": f"Bearer {MERIDIAN_API_KEY}"},
        json={"industries": industries},
        timeout=30,
    )
    result = resp.json()
    print(f"Industries sync: {result.get('upserted', 0)} upserted, {result.get('errors', 0)} errors")
    return result


def main():
    parser = argparse.ArgumentParser(description="Sync registries between Meridian and ClientBrain")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not MERIDIAN_API_KEY:
        print("ERROR: MERIDIAN_CLIENTBRAIN_API_KEY env var not set", file=sys.stderr)
        sys.exit(1)

    print("=== Pushing Meridian registries to ClientBrain ===")
    push_topics(args.dry_run)
    push_industries(args.dry_run)
    print()
    print("Done.")


if __name__ == "__main__":
    main()
