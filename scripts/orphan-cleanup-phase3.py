#!/usr/bin/env python3
"""Phase 3 orphan cleanup: unblock the skip list and add the promoted topics.

Runs against /meridian/synthesis_queue.json directly. This file is
gitignored and lives only on the VM, so it can't be deployed via git.

Actions:
  1. Flip the 7 skip entries to pending (amazon-advertising,
     b2b-marketing, hipaa-compliance, onboarding, reporting, shopify,
     zapier) — they all have real Layer 2 fragments and the new
     prompt handles sparse evidence via the Gaps section.
  2. Add four new pending entries for topics promoted to topics.yaml
     in Phase 2 (retargeting, influencer-marketing, event-marketing,
     data-quality) with a fragment_count derived from what's in the
     filesystem.
  3. Reset eight already-synthesized topics to pending because the
     industries/ merge added significant new content to their
     client-extractions.md files, making their existing Layer 3
     index.md stale: b2b-marketing (already flipped above),
     ecommerce-strategy, elearning, financial-operations, food-beverage,
     hipaa-compliance (already flipped), legal, nonprofit, saas,
     senior-living.
"""

from __future__ import annotations

import json
from pathlib import Path

QUEUE_PATH = Path("/meridian/synthesis_queue.json")
WK = Path("/meridian/wiki/knowledge")

SKIP_TO_UNBLOCK = {
    "amazon-advertising",
    "b2b-marketing",
    "hipaa-compliance",
    "onboarding",
    "reporting",
    "shopify",
    "zapier",
}

PROMOTED = [
    "retargeting",
    "influencer-marketing",
    "event-marketing",
    "data-quality",
]

STALE_POST_MERGE = [
    "ecommerce-strategy",
    "elearning",
    "financial-operations",
    "food-beverage",
    "legal",
    "nonprofit",
    "saas",
    "senior-living",
    # b2b-marketing and hipaa-compliance are already covered by
    # SKIP_TO_UNBLOCK — they're flipping from skip → pending.
]


def count_fragments(slug: str) -> int:
    d = WK / slug
    if not d.is_dir():
        return 0
    return sum(1 for f in d.glob("*.md") if f.name not in ("index.md", "_index.md"))


def main() -> None:
    queue = json.loads(QUEUE_PATH.read_text(encoding="utf-8"))
    by_slug = {item["topic"]: item for item in queue}

    # 1) Flip skip → pending
    for slug in SKIP_TO_UNBLOCK:
        if slug in by_slug:
            before = by_slug[slug].get("status")
            by_slug[slug]["status"] = "pending"
            by_slug[slug]["fragment_count"] = count_fragments(slug)
            print(f"  unblock {slug:<24} ({before} → pending, frags={by_slug[slug]['fragment_count']})")
        else:
            print(f"  [skip] {slug} not in queue")

    # 2) Add promoted topics
    for slug in PROMOTED:
        if slug in by_slug:
            before = by_slug[slug].get("status")
            by_slug[slug]["status"] = "pending"
            by_slug[slug]["fragment_count"] = count_fragments(slug)
            print(f"  promote {slug:<24} (already present: {before} → pending)")
        else:
            by_slug[slug] = {
                "topic": slug,
                "status": "pending",
                "fragment_count": count_fragments(slug),
                "priority": 5,
            }
            print(f"  promote {slug:<24} (added: frags={by_slug[slug]['fragment_count']})")

    # 3) Reset stale post-merge topics
    for slug in STALE_POST_MERGE:
        if slug in by_slug:
            before = by_slug[slug].get("status")
            by_slug[slug]["status"] = "pending"
            by_slug[slug]["fragment_count"] = count_fragments(slug)
            print(f"  restale {slug:<24} ({before} → pending, frags={by_slug[slug]['fragment_count']})")
        else:
            print(f"  [skip] {slug} not in queue")

    updated = list(by_slug.values())
    QUEUE_PATH.write_text(json.dumps(updated, indent=2), encoding="utf-8")

    # Summary
    counts: dict[str, int] = {}
    for item in updated:
        counts[item.get("status", "?")] = counts.get(item.get("status", "?"), 0) + 1
    print()
    print("queue status summary:")
    for k in sorted(counts):
        print(f"  {k:<12} {counts[k]}")
    print(f"  total      {len(updated)}")


if __name__ == "__main__":
    main()
