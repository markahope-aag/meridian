#!/usr/bin/env python3
"""Meridian Linter — wiki health checks, auto-fix, and flagging.

Usage:
    python agents/linter.py                          # full lint, auto-fix enabled
    python agents/linter.py --dry-run                # report only, no changes
    python agents/linter.py --scope contradictions   # specific check only
    python agents/linter.py --scope orphans
    python agents/linter.py --scope gaps
    python agents/linter.py --scope all

Output: JSON with actions taken, flags, and summary.
"""

import argparse
import json
import os
import re
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

import anthropic
import yaml


ROOT = Path(__file__).parent.parent
WIKI_DIR = ROOT / "wiki"
OUTPUTS_DIR = ROOT / "outputs"
PROMPTS_DIR = ROOT / "prompts"

_write_lock = threading.Lock()


def load_config() -> dict:
    with open(ROOT / "config.yaml") as f:
        return yaml.safe_load(f)


def load_prompt() -> str:
    return (PROMPTS_DIR / "linter.md").read_text(encoding="utf-8")


def load_index() -> str:
    path = WIKI_DIR / "_index.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def load_backlinks() -> str:
    path = WIKI_DIR / "_backlinks.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def load_all_articles() -> dict[str, str]:
    """Load all wiki articles as {relative_path: content}."""
    articles = {}
    if not WIKI_DIR.exists():
        return articles
    for md_file in WIKI_DIR.rglob("*.md"):
        if md_file.name in ("home.md",):
            continue
        try:
            rel = str(md_file.relative_to(ROOT))
            articles[rel] = md_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
    return articles


def now_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# LLM analysis
# ---------------------------------------------------------------------------

def run_llm_analysis(client: anthropic.Anthropic, index_md: str,
                     backlinks_md: str, articles: dict[str, str],
                     config: dict, scope: str) -> dict:
    """Send wiki content to LLM for analysis."""
    system_prompt = load_prompt()

    # Build article dump (cap total size)
    article_sections = []
    total_chars = 0
    for path, content in sorted(articles.items()):
        if total_chars > 150_000:
            article_sections.append(f"\n### {path}\n[... truncated, {len(articles) - len(article_sections)} more articles ...]")
            break
        article_sections.append(f"\n### {path}\n\n{content}")
        total_chars += len(content)

    scope_instruction = ""
    if scope != "all":
        scope_instruction = f"\n\nFocus ONLY on: {scope}. Return empty arrays for other categories."

    user_content = (
        f"## wiki/_index.md\n\n{index_md}\n\n"
        f"## wiki/_backlinks.md\n\n{backlinks_md}\n\n"
        f"## All Wiki Articles\n\n{''.join(article_sections)}"
        f"{scope_instruction}"
    )

    response = client.messages.create(
        model=config["llm"]["model"],
        max_tokens=8192,
        temperature=0.2,
        system=system_prompt,
        messages=[{"role": "user", "content": user_content}],
    )

    text = response.content[0].text
    json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if json_match:
        text = json_match.group(1)
    return json.loads(text)


# ---------------------------------------------------------------------------
# Auto-fix actions
# ---------------------------------------------------------------------------

def find_actual_links(articles: dict[str, str]) -> dict[str, set[str]]:
    """Build a map of article → set of articles it links to."""
    link_map = {}
    for path, content in articles.items():
        links = set()
        # Match [[wikilinks]]
        for match in re.finditer(r"\[\[([^\]|]+)", content):
            target = match.group(1)
            # Normalize: add wiki/ prefix if not present
            if not target.startswith("wiki/"):
                target = f"wiki/{target}"
            if not target.endswith(".md"):
                target += ".md"
            links.add(target)
        link_map[path] = links
    return link_map


def rebuild_backlinks(articles: dict[str, str]) -> str:
    """Rebuild _backlinks.md from actual link state."""
    link_map = find_actual_links(articles)
    now = now_str()

    # Invert: target → set of sources
    inbound = {}
    for source, targets in link_map.items():
        for target in targets:
            inbound.setdefault(target, set()).add(source)

    lines = [
        "---",
        f'title: "Backlink Registry"',
        "type: index",
        f'created: "2026-04-04"',
        f'updated: "{now}"',
        "---",
        "",
        "# Backlink Registry",
        "",
    ]

    for target in sorted(inbound.keys()):
        sources = sorted(inbound[target])
        lines.append(f"## {target}")
        for src in sources:
            lines.append(f"- [[{src}]]")
        lines.append("")

    return "\n".join(lines)


def find_missing_index_entries(articles: dict[str, str], index_md: str) -> list[str]:
    """Find articles not mentioned in _index.md."""
    missing = []
    for path in articles:
        if path in ("wiki/_index.md", "wiki/_backlinks.md", "wiki/log.md", "wiki/home.md"):
            continue
        # Check if any form of the path appears in the index
        short = path.replace("wiki/", "").replace(".md", "")
        if short not in index_md and path not in index_md:
            title_match = re.search(r'^title:\s*["\']?(.+?)["\']?\s*$',
                                    articles[path], re.MULTILINE)
            title = title_match.group(1) if title_match else Path(path).stem
            missing.append(f"- [[{short}]] — {title}")
    return missing


def create_stub(concept: str, slug: str, mentioned_in: list[str],
                location: str) -> str:
    """Create a stub article for a gap concept."""
    now = now_str()
    backlinks = "\n".join(f"- [[{p.replace('wiki/', '').replace('.md', '')}]]"
                          for p in mentioned_in[:10])
    return (
        f"---\n"
        f'title: "{concept}"\n'
        f"type: concept\n"
        f'created: "{now}"\n'
        f'updated: "{now}"\n'
        f"source_docs: []\n"
        f"tags: [stub]\n"
        f"---\n\n"
        f"# {concept}\n\n"
        f"_This is a stub article created by the linter. "
        f"Mentioned in {len(mentioned_in)} articles — "
        f"the compiler will flesh it out on the next run._\n\n"
        f"## Referenced in\n\n{backlinks}\n"
    )


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def generate_report(analysis: dict, actions: list[str], article_count: int,
                    dry_run: bool) -> str:
    """Generate the markdown lint report."""
    now = now_str()
    mode = "DRY RUN" if dry_run else "AUTO-FIX"

    lines = [
        f"# Meridian Wiki Health Check",
        f"",
        f"**Generated:** {now}  ",
        f"**Mode:** {mode}  ",
        f"**Articles scanned:** {article_count}  ",
        f"",
    ]

    # Actions taken
    if actions:
        lines.append(f"## Actions Taken ({len(actions)})\n")
        for action in actions:
            lines.append(f"- {action}")
        lines.append("")
    elif not dry_run:
        lines.append("## Actions Taken (0)\n\nNo auto-fixes needed.\n")

    # Contradictions
    contradictions = analysis.get("contradictions", [])
    if contradictions:
        lines.append(f"## Contradictions ({len(contradictions)})\n")
        for c in contradictions:
            lines.append(f"### {c.get('article_a', '?')} vs {c.get('article_b', '?')}")
            lines.append(f"**Claim in A:** {c.get('claim_a', '?')}")
            lines.append(f"**Claim in B:** {c.get('claim_b', '?')}")
            lines.append(f"**Recommended action:** {c.get('recommendation', '?')}")
            lines.append("")

    # Orphans
    orphans = analysis.get("orphans", [])
    if orphans:
        lines.append(f"## Orphans ({len(orphans)})\n")
        for o in orphans:
            lines.append(f"- [[{o.get('path', '?')}]] — {o.get('suggestion', '')}")
        lines.append("")

    # Gaps
    gaps = analysis.get("gaps", [])
    auto_gaps = [g for g in gaps if g.get("mention_count", 0) >= 5]
    flag_gaps = [g for g in gaps if 3 <= g.get("mention_count", 0) < 5]

    if auto_gaps:
        lines.append(f"## Auto-Created Stubs ({len(auto_gaps)})\n")
        for g in auto_gaps:
            lines.append(f"- **{g['concept']}** — mentioned in {g['mention_count']} articles → `{g.get('suggested_location', '?')}`")
        lines.append("")

    if flag_gaps:
        lines.append(f"## Article Candidates ({len(flag_gaps)})\n")
        for g in flag_gaps:
            mentioned = ", ".join(f"[[{p}]]" for p in g.get("mentioned_in", [])[:5])
            lines.append(f"- **{g['concept']}** ({g['mention_count']} mentions) — {mentioned}")
            lines.append(f"  Suggested location: `{g.get('suggested_location', '?')}`")
        lines.append("")

    # Suggested connections
    connections = analysis.get("suggested_connections", [])
    if connections:
        lines.append(f"## Suggested Connections ({len(connections)})\n")
        for c in connections:
            lines.append(f"- [[{c.get('article_a', '?')}]] ↔ [[{c.get('article_b', '?')}]] — {c.get('reason', '')}")
        lines.append("")

    # Client status changes
    status_changes = analysis.get("client_status_changes", [])
    if status_changes:
        lines.append(f"## Client Status Changes ({len(status_changes)})\n")
        for s in status_changes:
            lines.append(
                f"- **{s.get('client', '?')}** — signals suggest "
                f"{s.get('current_status', '?')} → {s.get('suggested_status', '?')}. "
                f"Signal: {s.get('signal', '?')}. Last activity: {s.get('last_activity', '?')}."
            )
        lines.append("")

    # Summary
    lines.append("## Summary\n")
    lines.append(f"- {len(actions)} auto-fixes applied")
    lines.append(f"- {len(contradictions)} contradictions flagged")
    lines.append(f"- {len(orphans)} orphans flagged")
    lines.append(f"- {len(auto_gaps)} stubs auto-created")
    lines.append(f"- {len(flag_gaps)} new article candidates")
    lines.append(f"- {len(connections)} connections suggested")
    lines.append(f"- {len(status_changes)} client status changes flagged")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Meridian Linter")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report only, no changes")
    parser.add_argument("--scope", default="all",
                        choices=["contradictions", "orphans", "gaps", "all"],
                        help="Which checks to run")
    args = parser.parse_args()

    config = load_config()
    articles = load_all_articles()

    # Filter out index files for article count
    content_articles = {k: v for k, v in articles.items()
                        if Path(k).name not in ("_index.md", "_backlinks.md",
                                                 "log.md", "home.md")}

    if len(content_articles) < 3:
        report = (
            f"# Meridian Wiki Health Check\n\n"
            f"**Generated:** {now_str()}\n\n"
            f"Not enough content to lint ({len(content_articles)} articles). "
            f"Minimum 3 articles needed.\n"
        )
        print(json.dumps({
            "status": "ok",
            "message": "not enough content to lint",
            "article_count": len(content_articles),
        }))
        return

    index_md = load_index()
    backlinks_md = load_backlinks()

    # Run LLM analysis
    print(f"Analyzing {len(content_articles)} wiki articles...", file=sys.stderr)
    client = anthropic.Anthropic()

    try:
        analysis = run_llm_analysis(
            client, index_md, backlinks_md, articles, config, args.scope
        )
    except Exception as e:
        print(f"LLM analysis failed: {e}", file=sys.stderr)
        analysis = {
            "contradictions": [], "orphans": [], "gaps": [],
            "suggested_connections": [], "client_status_changes": [],
        }

    # Apply auto-fixes (unless dry-run)
    actions = []

    if not args.dry_run:
        # 1. Rebuild backlinks
        new_backlinks = rebuild_backlinks(articles)
        if new_backlinks != backlinks_md:
            bl_path = WIKI_DIR / "_backlinks.md"
            bl_path.write_text(new_backlinks, encoding="utf-8")
            actions.append("Rebuilt _backlinks.md to match actual link state")

        # 2. Add missing index entries
        missing = find_missing_index_entries(articles, index_md)
        if missing:
            idx_path = WIKI_DIR / "_index.md"
            content = idx_path.read_text(encoding="utf-8")
            if "## Statistics" in content:
                content = content.replace(
                    "## Statistics",
                    "\n".join(missing) + "\n\n## Statistics"
                )
            else:
                content += "\n" + "\n".join(missing) + "\n"
            idx_path.write_text(content, encoding="utf-8")
            actions.append(f"Added {len(missing)} missing _index.md entries")

        # 3. Create stubs for gaps with 5+ mentions
        gaps = analysis.get("gaps", [])
        for gap in gaps:
            if gap.get("mention_count", 0) >= 5:
                location = gap.get("suggested_location", "")
                if not location:
                    continue
                stub_path = ROOT / location
                if not stub_path.exists():
                    stub_content = create_stub(
                        gap["concept"], gap.get("slug", ""),
                        gap.get("mentioned_in", []), location
                    )
                    stub_path.parent.mkdir(parents=True, exist_ok=True)
                    stub_path.write_text(stub_content, encoding="utf-8")
                    actions.append(
                        f"Created stub: {location} — mentioned in "
                        f"{gap['mention_count']} articles"
                    )

    # Generate report
    report = generate_report(analysis, actions, len(content_articles), args.dry_run)

    # Write reports
    now = now_str()
    if not args.dry_run:
        # Full report to outputs/
        OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
        out_path = OUTPUTS_DIR / f"lint-{now}.md"
        out_path.write_text(report, encoding="utf-8")

        # Condensed version to wiki/
        wiki_path = WIKI_DIR / "articles" / f"lint-{now}.md"
        wiki_path.parent.mkdir(parents=True, exist_ok=True)
        wiki_path.write_text(report, encoding="utf-8")

        # Append to log
        log_path = WIKI_DIR / "log.md"
        if log_path.exists():
            log_content = log_path.read_text(encoding="utf-8")
        else:
            log_content = ""
        summary = (
            f"{len(actions)} auto-fixes, "
            f"{len(analysis.get('contradictions', []))} contradictions, "
            f"{len(analysis.get('orphans', []))} orphans, "
            f"{len(analysis.get('gaps', []))} gaps"
        )
        log_content += f"\n## [{now}] lint | Wiki health check — {summary}\n"
        log_path.write_text(log_content, encoding="utf-8")

    output = {
        "status": "ok",
        "article_count": len(content_articles),
        "actions_taken": len(actions),
        "contradictions": len(analysis.get("contradictions", [])),
        "orphans": len(analysis.get("orphans", [])),
        "gaps": len(analysis.get("gaps", [])),
        "suggested_connections": len(analysis.get("suggested_connections", [])),
        "report": report,
    }
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
