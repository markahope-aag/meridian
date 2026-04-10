#!/usr/bin/env python3
"""Phase 1 orphan cleanup.

Three actions, all idempotent:

1. Delete empty meta-taxonomy `markets/` (3 empty sub-dirs).
2. Merge `industries/<industry>/client-extractions.md` into the
   canonical topic's client-extractions.md, then delete the orphan.
3. Consolidate the `abm`/`abm-marketing`/`account-based-marketing`
   cluster into `abm/`, and the `conversion-*` cluster into
   `attribution/` (already aliased in topics.yaml).

Every insight line is deduped against the target's existing content
by exact-match, so repeated runs never add duplicates.
"""

from __future__ import annotations

import shutil
from pathlib import Path

WK = Path("/meridian/wiki/knowledge")


def read_insights(path: Path) -> list[str]:
    """Bullet-point lines (`- `) from a client-extractions.md file."""
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    return [line for line in text.splitlines() if line.startswith("- ")]


def append_insights(canonical_slug: str, new_lines: list[str], source_label: str) -> int:
    """Append deduped insight lines to canonical/client-extractions.md."""
    if not new_lines:
        return 0
    dst_dir = WK / canonical_slug
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / "client-extractions.md"

    if dst.exists():
        existing = dst.read_text(encoding="utf-8", errors="replace")
    else:
        title = canonical_slug.replace("-", " ").title()
        existing = (
            "---\n"
            f"title: Client Extractions — {title}\n"
            "layer: 2\n"
            "type: article\n"
            "tags: [extraction]\n"
            "---\n\n"
            "# Client Extractions\n\n"
            "Transferable insights extracted from client engagements.\n\n"
        )

    existing_lines = {line for line in existing.splitlines() if line.startswith("- ")}
    additions = []
    for line in new_lines:
        if line not in existing_lines:
            additions.append(line)
            existing_lines.add(line)

    if additions:
        if not existing.endswith("\n"):
            existing += "\n"
        existing += "\n".join(additions) + "\n"
        dst.write_text(existing, encoding="utf-8")

    print(f"  + {canonical_slug}: +{len(additions)} lines (from {source_label})")
    return len(additions)


def rm_if_empty(d: Path) -> bool:
    """rmdir only if the directory is fully empty. Returns True on success."""
    try:
        d.rmdir()
        return True
    except OSError:
        return False


# ----- markets/ -----
print("=== markets/ tree ===")
markets = WK / "markets"
if markets.exists():
    for sub in sorted(markets.iterdir()):
        if sub.is_dir() and not any(sub.iterdir()):
            print(f"  del empty markets/{sub.name}/")
            sub.rmdir()
    if rm_if_empty(markets):
        print("  del markets/")

# ----- industries/ -----
print("=== industries/ tree ===")
INDUSTRY_MAP = {
    "b2b-services":       "b2b-marketing",
    "ecommerce":          "ecommerce-strategy",
    "elearning":          "elearning",
    "financial-services": "financial-operations",
    "food-beverage":      "food-beverage",
    "healthcare":         "hipaa-compliance",
    "legal-services":     "legal",
    "nonprofit":          "nonprofit",
    "saas":               "saas",
    "senior-living":      "senior-living",
}
industries = WK / "industries"
if industries.exists():
    for sub in sorted(industries.iterdir()):
        if not sub.is_dir():
            continue
        canon = INDUSTRY_MAP.get(sub.name)
        if canon is None:
            print(f"  [skip] no mapping for industries/{sub.name}")
            continue
        src = sub / "client-extractions.md"
        append_insights(canon, read_insights(src), f"industries/{sub.name}")
        if src.exists():
            src.unlink()
        if rm_if_empty(sub):
            print(f"  del industries/{sub.name}/")
    if rm_if_empty(industries):
        print("  del industries/")

# ----- abm cluster -----
print("=== abm cluster ===")
abm_sources = ["abm", "abm-marketing", "account-based-marketing"]
collected: list[str] = []
for name in abm_sources:
    collected.extend(read_insights(WK / name / "client-extractions.md"))
append_insights("abm", collected, " + ".join(abm_sources))
for name in ("abm-marketing", "account-based-marketing"):
    d = WK / name
    if d.exists():
        shutil.rmtree(d)
        print(f"  del {name}/")

# ----- conversion cluster → attribution -----
print("=== conversion cluster ===")
conv_sources = ["conversion-optimization", "conversion-rate-optimization", "conversion-tracking"]
collected = []
for name in conv_sources:
    collected.extend(read_insights(WK / name / "client-extractions.md"))
append_insights("attribution", collected, " + ".join(conv_sources))
for name in conv_sources:
    d = WK / name
    if d.exists():
        shutil.rmtree(d)
        print(f"  del {name}/")

# ----- summary -----
print()
remaining = sum(1 for d in WK.iterdir() if d.is_dir())
layer3 = sum(
    1
    for idx in WK.glob("*/index.md")
    if "layer: 3" in idx.read_text(encoding="utf-8", errors="replace")
)
print(f"total dirs under wiki/knowledge: {remaining}")
print(f"layer 3 synthesized:             {layer3}")
