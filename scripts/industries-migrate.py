#!/usr/bin/env python3
"""Migrate content into the new wiki/industries/<slug>/ dimension.

Three sources feed the new industries tree:

1. The restic snapshot c6ebbf57 (pre-orphan-cleanup) contains the
   original wiki/knowledge/industries/<industry>/client-extractions.md
   files with real insights. These were merged into topic dirs in
   Phase 1 of the orphan cleanup but should also exist as the seed
   for the industry dimension. We restore from the snapshot into a
   staging directory, then copy each industry's file into the
   canonical industries/<slug>/ location.

2. Five current "topics" that are actually industries move wholesale:
   senior-living, nonprofit, saas, elearning, food-beverage. Their
   Layer 2 fragments, Layer 3 index.md, and client-extractions.md
   all move from wiki/knowledge/<slug>/ to wiki/industries/<slug>/.

3. Industries with no existing content (construction-home-services,
   manufacturing) just get an empty directory so the dashboard has
   somewhere to land.

The script maps old slugs to new canonical slugs where they differ
(e.g. ecommerce → ecommerce-retail, legal-services stays,
healthcare stays). New slugs come from industries.yaml.

Idempotent: if the target dirs already exist with content, we append
rather than overwrite. Fragment files are copied, not moved, from the
restic staging dir (since restic is read-only).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import yaml

MERIDIAN = Path("/meridian")
WIKI = MERIDIAN / "wiki"
KNOWLEDGE = WIKI / "knowledge"
INDUSTRIES = WIKI / "industries"
RESTIC_STAGING = Path("/tmp/industries-restore")

# Map: old industries-tree subdir name (from restic) -> new canonical industries.yaml slug
RESTIC_RENAME = {
    "healthcare":         "healthcare",
    "senior-living":      "senior-living",
    "nonprofit":          "nonprofit",
    "saas":               "saas",
    "food-beverage":      "food-beverage",
    "elearning":          "elearning",
    "legal-services":     "legal-services",
    "b2b-services":       "b2b-services",
    "ecommerce":          "ecommerce-retail",       # renamed
    "financial-services": "financial-services",
}

# Industries that exist in industries.yaml but have no restic content —
# create empty placeholder dirs for dashboard navigation
BRAND_NEW_INDUSTRIES = [
    "manufacturing",
    "construction-home-services",
]

# Topics that move wholesale from wiki/knowledge/<slug>/ to
# wiki/industries/<slug>/ — they were miscategorized as topics.
TOPIC_TO_INDUSTRY_MIGRATIONS = [
    "senior-living",
    "nonprofit",
    "saas",
    "elearning",
    "food-beverage",
]


def ensure_industry_dir(slug: str) -> Path:
    d = INDUSTRIES / slug
    d.mkdir(parents=True, exist_ok=True)
    return d


def append_unique_insights(dst: Path, src_text: str, source_label: str) -> int:
    """Append bullet-point insight lines from src_text into dst, dedup by exact line."""
    new_lines = [l for l in src_text.splitlines() if l.startswith("- ")]
    if not new_lines:
        return 0

    if dst.exists():
        existing_text = dst.read_text(encoding="utf-8", errors="replace")
    else:
        title = dst.parent.name.replace("-", " ").title()
        existing_text = (
            "---\n"
            f"title: Client Extractions — {title}\n"
            "layer: 2\n"
            "type: article\n"
            "tags: [extraction]\n"
            "---\n\n"
            "# Client Extractions\n\n"
            "Transferable insights extracted from client engagements.\n\n"
        )

    existing = {l for l in existing_text.splitlines() if l.startswith("- ")}
    additions = [l for l in new_lines if l not in existing]
    if additions:
        if not existing_text.endswith("\n"):
            existing_text += "\n"
        existing_text += "\n".join(additions) + "\n"
        dst.write_text(existing_text, encoding="utf-8")
    print(f"  + {dst.parent.name}: +{len(additions)} insights (from {source_label})")
    return len(additions)


def restore_restic_snapshot() -> bool:
    """Restore wiki/knowledge/industries from the pre-cleanup snapshot."""
    if RESTIC_STAGING.exists():
        shutil.rmtree(RESTIC_STAGING)
    RESTIC_STAGING.mkdir(parents=True)
    cmd = [
        "restic",
        "restore",
        "c6ebbf57",
        "--target",
        str(RESTIC_STAGING),
        "--include",
        "/meridian/wiki/knowledge/industries",
    ]
    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    print(result.stdout[-500:])
    if result.returncode != 0:
        print(f"restic failed: {result.stderr}")
        return False
    staged = RESTIC_STAGING / "meridian" / "wiki" / "knowledge" / "industries"
    if not staged.exists():
        print(f"Expected path missing: {staged}")
        return False
    print(f"Restored to {staged}")
    return True


def import_from_restic():
    """Copy restored industries/ content into wiki/industries/."""
    staged = RESTIC_STAGING / "meridian" / "wiki" / "knowledge" / "industries"
    if not staged.exists():
        print("No restic staging dir — skipping import")
        return

    print("=== Importing restic snapshot content ===")
    for old_name, new_slug in RESTIC_RENAME.items():
        src = staged / old_name / "client-extractions.md"
        if not src.exists():
            print(f"  [skip] {old_name}/client-extractions.md not in snapshot")
            continue
        dst_dir = ensure_industry_dir(new_slug)
        dst = dst_dir / "client-extractions.md"
        append_unique_insights(dst, src.read_text(encoding="utf-8"), f"restic:{old_name}")


def migrate_topic_to_industry():
    """Move wiki/knowledge/<slug>/ contents into wiki/industries/<slug>/ for the 5 reclassified."""
    print("=== Migrating 5 topics to industries ===")
    for slug in TOPIC_TO_INDUSTRY_MIGRATIONS:
        src_dir = KNOWLEDGE / slug
        if not src_dir.exists():
            print(f"  [skip] wiki/knowledge/{slug}/ does not exist")
            continue
        dst_dir = ensure_industry_dir(slug)

        moved = 0
        for f in src_dir.iterdir():
            if not f.is_file():
                continue
            target = dst_dir / f.name
            if target.exists() and f.name == "client-extractions.md":
                # Merge instead of overwrite for client-extractions.md
                append_unique_insights(target, f.read_text(encoding="utf-8"), f"knowledge/{slug}")
                f.unlink()
                continue
            if target.exists():
                # Keep both by renaming
                target = dst_dir / f"from-topic-{f.name}"
            shutil.move(str(f), str(target))
            moved += 1

        # Try to remove the now-empty topic dir
        try:
            src_dir.rmdir()
            print(f"  migrated wiki/knowledge/{slug}/ → wiki/industries/{slug}/ ({moved} files)")
        except OSError:
            leftover = list(src_dir.iterdir())
            print(f"  migrated wiki/knowledge/{slug}/ ({moved} files) but dir still has {len(leftover)} items")


def create_empty_industries():
    """Create placeholder directories for brand-new industries so they show up in the dashboard."""
    print("=== Creating placeholder dirs for brand-new industries ===")
    for slug in BRAND_NEW_INDUSTRIES:
        d = ensure_industry_dir(slug)
        placeholder = d / "PLACEHOLDER.md"
        if not placeholder.exists():
            placeholder.write_text(
                "---\n"
                f"title: {slug.replace('-', ' ').title()}\n"
                "layer: 2\n"
                "type: placeholder\n"
                "---\n\n"
                f"# {slug.replace('-', ' ').title()}\n\n"
                f"No insights yet. Add client engagements in this vertical to populate.\n",
                encoding="utf-8",
            )
            print(f"  + {slug}/PLACEHOLDER.md")
        else:
            print(f"  = {slug}/ already exists")


def main() -> None:
    INDUSTRIES.mkdir(parents=True, exist_ok=True)

    ok = restore_restic_snapshot()
    if ok:
        import_from_restic()

    migrate_topic_to_industry()
    create_empty_industries()

    # Summary
    print()
    print("=== Final wiki/industries/ state ===")
    if INDUSTRIES.exists():
        for d in sorted(INDUSTRIES.iterdir()):
            if d.is_dir():
                n = sum(1 for f in d.glob("*.md"))
                print(f"  {d.name:<30} {n} files")

    if RESTIC_STAGING.exists():
        shutil.rmtree(RESTIC_STAGING)


if __name__ == "__main__":
    main()
