#!/usr/bin/env python3
"""Rebuild client folders as thin stubs after extraction.

Deletes all articles in each client folder and replaces with a single _index.md
linking to the knowledge articles that cite this client.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
WIKI_DIR = ROOT / "wiki"

CLIENT_INDUSTRY = {
    "doudlah-farms": "food-beverage", "didion": "food-beverage",
    "bake-believe": "food-beverage", "citrus-america": "food-beverage",
    "adava-care": "healthcare", "ahs": "healthcare",
    "cordwainer": "senior-living", "skaalen": "senior-living",
    "aviary": "saas", "hazardos": "saas", "pema": "saas", "finwellu": "saas",
    "papertube": "ecommerce", "crazy-lennys": "ecommerce", "lamarie": "ecommerce",
    "bluepoint": "b2b-services", "american-extractions": "b2b-services",
    "trachte": "b2b-services", "exterior-renovations": "b2b-services",
    "overhead-door": "b2b-services", "reynolds": "b2b-services",
    "sonoplot": "b2b-services", "seamless": "b2b-services",
    "quarra": "b2b-services", "agility-recovery": "elearning",
    "axley": "legal-services", "capitol-bank": "financial-services",
    "blue-sky": "financial-services", "vcedc": "nonprofit",
    "maple-bluff": "nonprofit", "wi-masons": "nonprofit",
}


def find_knowledge_mentions(client_name: str) -> list[tuple[str, int]]:
    """Find knowledge articles that cite this client. Returns (topic, mention_count)."""
    mentions = {}
    knowledge_dir = WIKI_DIR / "knowledge"
    if not knowledge_dir.exists():
        return []

    for extractions_file in knowledge_dir.rglob("client-extractions.md"):
        try:
            content = extractions_file.read_text(encoding="utf-8", errors="replace")
            count = content.count(f"[[{client_name},")
            if count > 0:
                # Topic is the parent folder name
                topic = extractions_file.parent.name
                # Handle industries/ subfolder
                if "industries/" in str(extractions_file):
                    topic = f"industries/{topic}"
                mentions[topic] = count
        except Exception:
            continue

    return sorted(mentions.items(), key=lambda x: -x[1])


def get_date_range(client_dir: Path) -> tuple[str, str]:
    """Get first and last activity dates from filenames."""
    dates = []
    for f in client_dir.glob("*.md"):
        # Extract date from filename (YYYY-MM-DD-...)
        parts = f.stem.split("-")
        if len(parts) >= 3:
            try:
                date = f"{parts[0]}-{parts[1]}-{parts[2]}"
                # Validate it looks like a date
                if len(date) == 10 and date[4] == "-" and date[7] == "-":
                    dates.append(date)
            except Exception:
                pass
    if not dates:
        return "", ""
    return min(dates), max(dates)


def rebuild_stub(client_dir: Path, status: str):
    """Rebuild one client folder as a thin stub."""
    client_slug = client_dir.name
    client_name = client_slug.replace("-", " ").title()
    industry = CLIENT_INDUSTRY.get(client_slug, "")

    # Find knowledge mentions before deleting
    mentions = find_knowledge_mentions(client_name)

    # Get date range from existing articles
    first_seen, last_activity = get_date_range(client_dir)

    # Build new _index.md
    fm_lines = [
        "---",
        f'title: "{client_name}"',
        "layer: 2",
        f"status: {status}",
    ]
    if industry:
        fm_lines.append(f'industry: "{industry}"')
    if first_seen:
        fm_lines.append(f'first_engagement: "{first_seen}"')
    if last_activity:
        fm_lines.append(f'last_activity: "{last_activity}"')
    fm_lines.append("in_clientbrain: true")
    fm_lines.append("---")

    body_lines = [
        "",
        f"# {client_name}",
        "",
        "*Full client record and work history: ClientBrain*",
        "",
        "## What We Learned",
        "",
    ]

    if mentions:
        body_lines.append(
            "Knowledge extracted from this engagement contributes to:"
        )
        body_lines.append("")
        for topic, count in mentions[:20]:
            topic_display = topic.replace("-", " ").replace("industries/", "").title()
            body_lines.append(f"- [[knowledge/{topic}/client-extractions|{topic_display}]] ({count} insights)")
        body_lines.append("")
    else:
        body_lines.append("*No extracted insights yet.*")
        body_lines.append("")

    if industry:
        body_lines.append("## Industry Context")
        body_lines.append("")
        body_lines.append(f"- [[knowledge/industries/{industry}/client-extractions|{industry.replace('-', ' ').title()}]]")
        body_lines.append("")

    content = "\n".join(fm_lines + body_lines)

    # Delete all existing files in the client folder (not just .md)
    for f in client_dir.iterdir():
        if f.is_file():
            f.unlink()
        elif f.is_dir():
            import shutil
            shutil.rmtree(f)

    # Write the new stub
    index_path = client_dir / "_index.md"
    index_path.write_text(content, encoding="utf-8")

    return {"client": client_slug, "mentions": len(mentions), "total_insights": sum(c for _, c in mentions)}


def main():
    results = []
    for status_dir in ["current", "former", "prospects"]:
        base = WIKI_DIR / "clients" / status_dir
        if not base.exists():
            continue
        for client_dir in sorted(base.iterdir()):
            if not client_dir.is_dir():
                continue
            try:
                result = rebuild_stub(client_dir, status_dir)
                result["status"] = status_dir
                results.append(result)
                print(f"  {client_dir.name}: {result['mentions']} topics, "
                      f"{result['total_insights']} insights",
                      file=sys.stderr)
            except Exception as e:
                print(f"  ERROR {client_dir.name}: {e}", file=sys.stderr)

    print(json.dumps({"status": "ok", "rebuilt": len(results), "clients": results}, indent=2))


if __name__ == "__main__":
    main()
