#!/usr/bin/env python3
"""Audit client folders — classify articles as operational/learning/industry/reference.

Usage:
    python scripts/audit-clients.py              # full audit
    python scripts/audit-clients.py --client doudlah-farms  # single client

Output: outputs/client-audit.md
"""

import json
import os
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
WIKI_DIR = ROOT / "wiki"
OUTPUTS_DIR = ROOT / "outputs"


def classify_by_title(title: str, filename: str) -> str:
    """Heuristic classification from title/filename alone — no LLM needed."""
    title_lower = title.lower()
    fname_lower = filename.lower()

    # Operational signals
    operational_signals = [
        "marketing-call", "weekly-call", "monthly-call", "stand-up",
        "status-update", "deliverable", "timeline", "invoice",
        "retainer", "contract", "onboarding", "offboarding",
        "campaign-setup", "account-setup", "access-", "login",
        "sprint-planning", "task-list", "handoff",
    ]
    for sig in operational_signals:
        if sig in fname_lower or sig in title_lower:
            return "operational"

    # Reference signals
    reference_signals = ["_index", "client-index", "overview"]
    for sig in reference_signals:
        if sig in fname_lower:
            return "reference"

    # Industry signals
    industry_signals = [
        "industry", "market-analysis", "competitive-landscape",
        "regulatory", "compliance", "buyer-behavior",
    ]
    for sig in industry_signals:
        if sig in fname_lower or sig in title_lower:
            return "industry"

    # Learning signals (most articles with strategy/analysis content)
    learning_signals = [
        "strategy", "analysis", "performance", "optimization",
        "what-works", "pattern", "insight", "audit", "review",
        "decision", "evaluation", "recommendation", "framework",
        "approach", "methodology", "integration", "architecture",
        "troubleshoot", "diagnosis", "resolution", "migration",
    ]
    for sig in learning_signals:
        if sig in fname_lower or sig in title_lower:
            return "learning"

    # Default: classify by word count later
    return "needs_review"


def get_suggested_topic(tags: list, title: str) -> str:
    """Suggest a canonical topic based on tags and title."""
    # Load topics registry
    topics_path = ROOT / "topics.yaml"
    if not topics_path.exists():
        return ""

    with open(topics_path) as f:
        data = yaml.safe_load(f) or {}

    # Build alias → slug map
    alias_map = {}
    for item in data.get("categories", []):
        slug = item.get("slug", "")
        alias_map[slug] = slug
        for alias in item.get("aliases", []):
            alias_map[alias.lower()] = slug

    # Check tags
    for tag in tags:
        if tag.lower() in alias_map:
            return alias_map[tag.lower()]

    # Check title words
    title_words = title.lower().replace("-", " ").split()
    for word in title_words:
        if word in alias_map:
            return alias_map[word]

    return ""


def audit_all_clients(target_client: str = None):
    """Audit all client folders and classify articles."""
    results = []

    for status_dir in ["current", "former", "prospects"]:
        base = WIKI_DIR / "clients" / status_dir
        if not base.exists():
            continue

        for client_dir in sorted(base.iterdir()):
            if not client_dir.is_dir():
                continue
            client = client_dir.name

            if target_client and client != target_client:
                continue

            for f in sorted(client_dir.glob("*.md")):
                if f.name == "_index.md":
                    continue

                try:
                    content = f.read_text(encoding="utf-8", errors="replace")
                    fm = {}
                    if content.startswith("---"):
                        parts = content.split("---", 2)
                        if len(parts) >= 3:
                            try:
                                fm = yaml.safe_load(parts[1]) or {}
                            except yaml.YAMLError:
                                pass

                    title = fm.get("title", f.stem)
                    tags = fm.get("tags", [])
                    words = len(content.split())

                    classification = classify_by_title(title, f.name)

                    # For needs_review, use word count heuristic
                    if classification == "needs_review":
                        if words < 300:
                            classification = "operational"
                        else:
                            classification = "learning"

                    topic = get_suggested_topic(tags, title)

                    results.append({
                        "status": status_dir,
                        "client": client,
                        "filename": f.name,
                        "title": title,
                        "words": words,
                        "classification": classification,
                        "suggested_topic": topic,
                        "tags": tags[:5],
                    })
                except Exception as e:
                    print(f"Error reading {f}: {e}", file=sys.stderr)

    return results


def write_audit_report(results: list):
    """Write the audit report to outputs/."""
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = OUTPUTS_DIR / "client-audit.md"

    # Summary stats
    by_class = {}
    by_client = {}
    for r in results:
        c = r["classification"]
        by_class[c] = by_class.get(c, 0) + 1
        client = r["client"]
        if client not in by_client:
            by_client[client] = {"operational": 0, "learning": 0, "industry": 0, "reference": 0}
        by_client[client][c] = by_client[client].get(c, 0) + 1

    lines = [
        "# Client Folder Audit",
        "",
        f"**Total articles:** {len(results)}",
        "",
        "## Classification Summary",
        "",
        "| Classification | Count | % |",
        "|---|---|---|",
    ]
    for cls in ["operational", "learning", "industry", "reference"]:
        count = by_class.get(cls, 0)
        pct = round(count / len(results) * 100, 1) if results else 0
        lines.append(f"| {cls} | {count} | {pct}% |")

    lines.extend(["", "## By Client", "", "| Client | Operational | Learning | Industry | Total |",
                   "|---|---|---|---|---|"])
    for client in sorted(by_client.keys()):
        d = by_client[client]
        total = sum(d.values())
        lines.append(f"| {client} | {d.get('operational',0)} | {d.get('learning',0)} | {d.get('industry',0)} | {total} |")

    lines.extend(["", "## Full Article List", "",
                   "| Client | Article | Classification | Words | Suggested Topic |",
                   "|---|---|---|---|---|"])
    for r in results:
        lines.append(f"| {r['client']} | {r['filename']} | {r['classification']} | {r['words']} | {r['suggested_topic']} |")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Audit written to {report_path}", file=sys.stderr)
    return report_path


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Audit client folders")
    parser.add_argument("--client", help="Audit a single client")
    args = parser.parse_args()

    results = audit_all_clients(args.client)
    report = write_audit_report(results)

    # Print summary
    by_class = {}
    for r in results:
        c = r["classification"]
        by_class[c] = by_class.get(c, 0) + 1

    output = {
        "status": "ok",
        "total_articles": len(results),
        "classification": by_class,
        "report": str(report),
    }
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
