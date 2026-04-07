#!/usr/bin/env python3
"""Tag existing wiki articles with Layer 2 frontmatter.

Adds layer, client_source, industry_context, and transferable fields
to all articles in wiki/knowledge/ and wiki/clients/.

Usage:
    python scripts/tag-layers.py --dry-run    # show what would be tagged
    python scripts/tag-layers.py              # tag for real
"""

import argparse
import re
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).parent.parent
WIKI_DIR = ROOT / "wiki"


def load_client_registry() -> dict:
    """Load clients.yaml and build slug → {name, industry} map."""
    path = ROOT / "clients.yaml"
    if not path.exists():
        return {}
    with open(path) as f:
        data = yaml.safe_load(f) or {}

    # Industry mapping by client (inferred from services/context)
    industry_map = {
        "doudlah-farms": "food-beverage",
        "citrus-america": "food-beverage",
        "skaalen": "senior-living",
        "cordwainer": "senior-living",
        "adava-care": "healthcare",
        "ahs": "saas",
        "hazardos": "saas",
        "aviary": "saas",
        "didion": "food-beverage",
        "quarra": "b2b-services",
        "trachte": "b2b-services",
        "bluepoint": "b2b-services",
        "papertube": "ecommerce",
        "crazy-lennys": "ecommerce",
        "exterior-renovations": "b2b-services",
        "overhead-door": "b2b-services",
        "reynolds": "b2b-services",
        "sonoplot": "b2b-services",
        "axley": "legal-services",
        "wi-masons": "nonprofit",
        "agility-recovery": "elearning",
        "finwellu": "b2b-services",
        "avant-gardening": "b2b-services",
        "lamarie": "ecommerce",
        "flynn-audio": "b2b-services",
        "pema": "saas",
        "seamless": "b2b-services",
        "sbs": "b2b-services",
        "vcedc": "nonprofit",
        "maple-bluff": "nonprofit",
        "new-dawn-shine": "b2b-services",
        "american-extractions": "b2b-services",
        "jbf": "b2b-services",
        "three-gaits": "b2b-services",
        "blue-sky": "b2b-services",
        "hooper": "b2b-services",
        "ab-hooper": "b2b-services",
        "asymmetric": "b2b-services",
        "capitol-bank": "b2b-services",
        "global-coin": "b2b-services",
        "bake-believe": "food-beverage",
        "machinery-source": "b2b-services",
    }

    registry = {}
    for client in data.get("clients", []):
        slug = client.get("slug", "")
        registry[slug] = {
            "name": client.get("name", ""),
            "industry": industry_map.get(slug, ""),
            "status": client.get("status", "current"),
        }
    return registry


def parse_frontmatter(content: str) -> tuple[dict, str, str]:
    """Parse YAML frontmatter from markdown. Returns (frontmatter_dict, fm_text, body)."""
    if not content.startswith("---"):
        return {}, "", content

    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, "", content

    try:
        fm = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        fm = {}

    return fm, parts[1], parts[2]


def add_layer2_fields(fm: dict, file_path: Path, client_registry: dict) -> dict:
    """Add Layer 2 fields to frontmatter based on file path and registry."""
    if fm.get("layer"):
        return fm  # already tagged

    fm["layer"] = 2

    # Determine client_source from path
    rel = str(file_path.relative_to(WIKI_DIR))
    client_source = None
    industry_context = None
    transferable = True

    if "clients/" in rel:
        # Extract client slug from path: clients/current/slug/...
        parts = rel.split("/")
        if len(parts) >= 3:
            client_slug = parts[2]
            client_info = client_registry.get(client_slug, {})
            client_source = client_info.get("name") or client_slug
            industry_context = client_info.get("industry") or None
            transferable = False  # client-specific by default

    elif "knowledge/" in rel:
        transferable = True
        # Infer industry from topic path
        if any(ind in rel for ind in ["food-beverage", "senior-living", "nonprofit",
                                       "saas", "ecommerce", "healthcare",
                                       "legal-services", "b2b-services"]):
            parts = rel.split("/")
            for p in parts:
                if p in ["food-beverage", "senior-living", "nonprofit", "saas",
                         "ecommerce", "healthcare", "legal-services", "b2b-services"]:
                    industry_context = p
                    break

    fm["client_source"] = client_source
    fm["industry_context"] = industry_context
    fm["transferable"] = transferable

    return fm


def rebuild_content(fm: dict, body: str) -> str:
    """Rebuild markdown with updated frontmatter."""
    fm_str = yaml.dump(fm, default_flow_style=False, sort_keys=False,
                       allow_unicode=True).strip()
    return f"---\n{fm_str}\n---{body}"


def main():
    parser = argparse.ArgumentParser(description="Tag wiki articles with Layer 2 frontmatter")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be tagged")
    args = parser.parse_args()

    client_registry = load_client_registry()
    print(f"Loaded {len(client_registry)} clients from registry", file=sys.stderr)

    # Find all taggable articles
    targets = []
    for pattern in ["knowledge/**/*.md", "clients/**/*.md", "articles/*.md", "concepts/*.md"]:
        targets.extend(WIKI_DIR.glob(pattern))

    # Filter out index/system files
    targets = [t for t in targets if t.name not in
               ("_index.md", "_backlinks.md", "log.md", "home.md")]

    tagged = 0
    skipped = 0
    errors = 0

    for filepath in sorted(targets):
        try:
            content = filepath.read_text(encoding="utf-8", errors="replace")
            fm, fm_text, body = parse_frontmatter(content)

            if fm.get("layer"):
                skipped += 1
                continue

            updated_fm = add_layer2_fields(fm.copy(), filepath, client_registry)

            if args.dry_run:
                rel = str(filepath.relative_to(WIKI_DIR))
                cs = updated_fm.get("client_source") or "-"
                ind = updated_fm.get("industry_context") or "-"
                tr = updated_fm.get("transferable", "-")
                print(f"  {rel}: client={cs}, industry={ind}, transferable={tr}")
                tagged += 1
                continue

            new_content = rebuild_content(updated_fm, body)
            filepath.write_text(new_content, encoding="utf-8")
            tagged += 1

        except Exception as e:
            print(f"  ERROR {filepath}: {e}", file=sys.stderr)
            errors += 1

    print(f"\n{'DRY RUN — ' if args.dry_run else ''}Results:", file=sys.stderr)
    print(f"  Tagged: {tagged}", file=sys.stderr)
    print(f"  Skipped (already tagged): {skipped}", file=sys.stderr)
    print(f"  Errors: {errors}", file=sys.stderr)
    print(f"  Total processed: {tagged + skipped + errors}", file=sys.stderr)


if __name__ == "__main__":
    main()
