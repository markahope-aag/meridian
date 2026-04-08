#!/usr/bin/env python3
"""Extract transferable knowledge from client articles into wiki/knowledge/.

Usage:
    python scripts/extract-client-learnings.py --dry-run --clients doudlah-farms,bluepoint,quarra
    python scripts/extract-client-learnings.py --clients doudlah-farms
    python scripts/extract-client-learnings.py  # all clients

Output: JSON summary of extractions.
"""

import argparse
import json
import os
import re
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import anthropic
import yaml

ROOT = Path(__file__).parent.parent
WIKI_DIR = ROOT / "wiki"
OUTPUTS_DIR = ROOT / "outputs"
PROMPTS_DIR = ROOT / "prompts"

_write_lock = threading.Lock()

# Client → industry mapping
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

EXTRACTION_PROMPT = """You are extracting transferable knowledge from a client article.

The client is the SOURCE of the learning, not the subject. Extract insights that
apply beyond this specific client.

## Input
- Client name and industry
- Article content

## Extract
For each distinct insight in the article, output:

1. **insight**: The transferable learning (one clear sentence)
2. **evidence**: The specific evidence from this client (metrics, outcomes, what happened)
3. **topic**: The canonical knowledge topic this belongs to (from the provided list)
4. **industry_relevant**: true if this insight is specific to the client's industry
5. **confidence**: low (single observation), medium (strong evidence), high (validated pattern)

## Rules
- Extract ONLY transferable insights, not operational details
- "We set up Google Ads for Acme" is NOT an insight
- "Maximize Conversions bid strategy requires 15+ historical conversions before it functions reliably" IS an insight
- Each insight must be usable without knowing the client name
- The client name appears only in the citation, not in the insight text

## Output JSON only:
```json
{
  "insights": [
    {
      "insight": "the transferable learning",
      "evidence": "specific evidence from this client",
      "topic": "canonical-topic-slug",
      "industry_relevant": false,
      "confidence": "low"
    }
  ]
}
```

If the article contains no transferable insights (pure operational content), return:
```json
{"insights": []}
```

## Canonical topics (use only these):
"""


def load_topic_slugs() -> str:
    """Load canonical topic slugs for the prompt."""
    path = ROOT / "topics.yaml"
    if not path.exists():
        return ""
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    slugs = [item.get("slug", "") for item in data.get("categories", [])]
    return ", ".join(slugs)


def load_config() -> dict:
    with open(ROOT / "config.yaml") as f:
        return yaml.safe_load(f)


def read_article(filepath: Path) -> dict:
    """Read article and return structured data."""
    content = filepath.read_text(encoding="utf-8", errors="replace")
    fm = {}
    body = content
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            try:
                fm = yaml.safe_load(parts[1]) or {}
            except yaml.YAMLError:
                pass
            body = parts[2].strip()
    return {
        "path": str(filepath),
        "filename": filepath.name,
        "title": fm.get("title", filepath.stem),
        "tags": fm.get("tags", []),
        "created": fm.get("created", ""),
        "words": len(body.split()),
        "body": body,
    }


def extract_from_article(client: anthropic.Anthropic, article: dict,
                          client_name: str, client_industry: str,
                          topic_slugs: str, config: dict) -> list[dict]:
    """Use Haiku to extract transferable insights from one article."""
    planning_model = config.get("compiler", {}).get(
        "planning_model", "claude-haiku-4-5-20251001"
    )

    body = article["body"]
    if len(body) > 6000:
        body = body[:6000] + "\n[... truncated ...]"

    prompt = EXTRACTION_PROMPT + topic_slugs

    response = client.messages.create(
        model=planning_model,
        max_tokens=4096,
        temperature=0.2,
        system=prompt,
        messages=[{
            "role": "user",
            "content": (
                f"## Client: {client_name}\n"
                f"## Industry: {client_industry}\n"
                f"## Article: {article['title']}\n\n"
                f"{body}"
            ),
        }],
    )

    text = response.content[0].text.strip()
    # Strip code block
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)

    try:
        data = json.loads(text)
        return data.get("insights", [])
    except json.JSONDecodeError:
        # Try brace matching
        start = text.find("{")
        if start >= 0:
            depth = 0
            for i in range(start, len(text)):
                if text[i] == "{": depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            data = json.loads(text[start:i+1])
                            return data.get("insights", [])
                        except json.JSONDecodeError:
                            break
        return []


def get_client_articles(client_slug: str) -> list[Path]:
    """Get all learning articles for a client."""
    articles = []
    for status_dir in ["current", "former", "prospects"]:
        client_dir = WIKI_DIR / "clients" / status_dir / client_slug
        if not client_dir.exists():
            continue
        for f in sorted(client_dir.glob("*.md")):
            if f.name == "_index.md":
                continue
            # Skip short operational articles
            content = f.read_text(encoding="utf-8", errors="replace")
            words = len(content.split())
            if words >= 250:
                articles.append(f)
    return articles


def write_insight_to_topic(insight: dict, client_name: str, source_date: str):
    """Append an extracted insight to the relevant wiki/knowledge/ topic."""
    topic = insight.get("topic", "")
    if not topic:
        return False

    topic_dir = WIKI_DIR / "knowledge" / topic
    topic_dir.mkdir(parents=True, exist_ok=True)

    # Write to a client-extractions file within the topic
    extractions_file = topic_dir / "client-extractions.md"

    with _write_lock:
        if extractions_file.exists():
            content = extractions_file.read_text(encoding="utf-8", errors="replace")
        else:
            content = (
                f"---\ntitle: Client Extractions — {topic.replace('-', ' ').title()}\n"
                f"layer: 2\ntype: article\ncreated: \"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}\"\n"
                f"updated: \"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}\"\ntags: [extraction]\n---\n\n"
                f"# Client Extractions\n\nTransferable insights extracted from client engagements.\n\n"
            )

        entry = (
            f"- {insight['insight']} "
            f"[[{client_name}, {source_date}]] "
            f"*({insight.get('confidence', 'low')})*\n"
        )

        if entry not in content:
            content += entry
            extractions_file.write_text(content, encoding="utf-8")

    return True


def write_industry_insight(insight: dict, client_name: str, industry: str, source_date: str):
    """Write industry-specific insight to wiki/knowledge/industries/."""
    if not industry:
        return False

    industry_dir = WIKI_DIR / "knowledge" / "industries" / industry
    industry_dir.mkdir(parents=True, exist_ok=True)

    extractions_file = industry_dir / "client-extractions.md"

    with _write_lock:
        if extractions_file.exists():
            content = extractions_file.read_text(encoding="utf-8", errors="replace")
        else:
            content = (
                f"---\ntitle: {industry.replace('-', ' ').title()} — Client Extractions\n"
                f"layer: 2\ntype: article\ncreated: \"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}\"\n"
                f"updated: \"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}\"\ntags: [extraction, industry]\n---\n\n"
                f"# {industry.replace('-', ' ').title()} — Client Extractions\n\n"
                f"Industry insights extracted from client engagements.\n\n"
            )

        entry = (
            f"- {insight['insight']} "
            f"[[{client_name}, {source_date}]] "
            f"*({insight.get('confidence', 'low')})*\n"
        )

        if entry not in content:
            content += entry
            extractions_file.write_text(content, encoding="utf-8")

    return True


def append_log(message: str):
    """Append to wiki/log.md."""
    log_path = WIKI_DIR / "log.md"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with _write_lock:
        content = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
        content += f"\n## [{now}] extract | {message}\n"
        log_path.write_text(content, encoding="utf-8")


def run_extraction(clients: list[str], dry_run: bool = False):
    """Run extraction for specified clients."""
    config = load_config()
    topic_slugs = load_topic_slugs()
    api_client = anthropic.Anthropic()

    all_results = {}
    topic_counts = {}
    industry_counts = {}
    total_insights = 0
    total_articles = 0
    failed = []
    samples = []

    for client_slug in clients:
        client_name = client_slug.replace("-", " ").title()
        client_industry = CLIENT_INDUSTRY.get(client_slug, "b2b-services")

        articles = get_client_articles(client_slug)
        print(f"\n{client_name}: {len(articles)} articles to process", file=sys.stderr)

        client_insights = []
        for i, filepath in enumerate(articles):
            article = read_article(filepath)
            print(f"  [{i+1}/{len(articles)}] {article['filename']}...", end=" ", file=sys.stderr)

            try:
                insights = extract_from_article(
                    api_client, article, client_name, client_industry,
                    topic_slugs, config
                )
                print(f"{len(insights)} insights", file=sys.stderr)

                for ins in insights:
                    ins["client"] = client_slug
                    ins["client_name"] = client_name
                    ins["source_article"] = article["filename"]
                    ins["source_date"] = article.get("created", "")

                    # Count by topic
                    topic = ins.get("topic", "")
                    topic_counts[topic] = topic_counts.get(topic, 0) + 1

                    # Count by industry
                    if ins.get("industry_relevant"):
                        industry_counts[client_industry] = industry_counts.get(client_industry, 0) + 1

                    # Write insights (unless dry run)
                    if not dry_run:
                        write_insight_to_topic(ins, client_name, ins.get("source_date", ""))
                        if ins.get("industry_relevant"):
                            write_industry_insight(ins, client_name, client_industry, ins.get("source_date", ""))

                client_insights.extend(insights)
                total_articles += 1
                time.sleep(0.3)  # rate limit

                # Collect samples from first client
                if client_slug == clients[0] and len(samples) < 5 and insights:
                    samples.append(insights[0])

            except Exception as e:
                print(f"ERROR: {e}", file=sys.stderr)
                failed.append({"client": client_slug, "article": article["filename"], "error": str(e)})

        # Per-client topics enriched
        client_topics = {}
        for ins in client_insights:
            t = ins.get("topic", "")
            client_topics[t] = client_topics.get(t, 0) + 1

        all_results[client_slug] = {
            "articles_processed": len(articles),
            "insights_extracted": len(client_insights),
            "topics_enriched": client_topics,
            "insights": client_insights if dry_run else [],
        }
        total_insights += len(client_insights)

        # Log per-client completion
        if not dry_run:
            top_topics = sorted(client_topics.items(), key=lambda x: -x[1])[:5]
            topics_str = ", ".join(f"{t}({c})" for t, c in top_topics)
            append_log(
                f"{client_name}: {len(articles)} articles, "
                f"{len(client_insights)} insights extracted. "
                f"Top topics: {topics_str}"
            )
            print(f"\n  {client_name} complete: {len(client_insights)} insights → {len(client_topics)} topics",
                  file=sys.stderr)

    # Build summary
    output = {
        "status": "ok",
        "dry_run": dry_run,
        "clients_processed": len(clients),
        "total_articles": total_articles,
        "total_insights": total_insights,
        "failed_extractions": len(failed),
        "topics_affected": dict(sorted(topic_counts.items(), key=lambda x: -x[1])),
        "industry_insights": industry_counts,
        "per_client": {k: {"articles": v["articles_processed"], "insights": v["insights_extracted"]}
                       for k, v in all_results.items()},
        "sample_insights": samples[:5],
        "failures": failed[:10],
    }

    # Write dry run report
    if dry_run:
        OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
        report_path = OUTPUTS_DIR / "extraction-dry-run.md"
        lines = [
            "# Client Knowledge Extraction — Dry Run",
            "",
            f"**Clients:** {', '.join(clients)}",
            f"**Articles processed:** {total_articles}",
            f"**Insights extracted:** {total_insights}",
            f"**Failed:** {len(failed)}",
            "",
            "## Per Client",
            "",
            "| Client | Articles | Insights |",
            "|---|---|---|",
        ]
        for k, v in all_results.items():
            lines.append(f"| {k} | {v['articles_processed']} | {v['insights_extracted']} |")

        lines.extend(["", "## Topics That Would Receive New Evidence", "",
                       "| Topic | New Insights |", "|---|---|"])
        for topic, count in sorted(topic_counts.items(), key=lambda x: -x[1]):
            lines.append(f"| {topic} | {count} |")

        lines.extend(["", "## Industry Knowledge", "",
                       "| Industry | New Insights |", "|---|---|"])
        for ind, count in sorted(industry_counts.items(), key=lambda x: -x[1]):
            lines.append(f"| {ind} | {count} |")

        if samples:
            lines.extend(["", "## Sample Insights", ""])
            for i, s in enumerate(samples, 1):
                lines.append(f"### Sample {i}")
                lines.append(f"**Insight:** {s.get('insight', '')}")
                lines.append(f"**Evidence:** {s.get('evidence', '')}")
                lines.append(f"**Topic:** {s.get('topic', '')}")
                lines.append(f"**Industry relevant:** {s.get('industry_relevant', False)}")
                lines.append(f"**Source:** {s.get('source_article', '')}")
                lines.append("")

        if failed:
            lines.extend(["", "## Failures", ""])
            for f2 in failed:
                lines.append(f"- {f2['client']}/{f2['article']}: {f2['error'][:100]}")

        report_path.write_text("\n".join(lines), encoding="utf-8")
        print(f"\nDry run report: {report_path}", file=sys.stderr)

    return output


def main():
    parser = argparse.ArgumentParser(description="Extract client learnings")
    parser.add_argument("--clients", help="Comma-separated client slugs")
    parser.add_argument("--dry-run", action="store_true", help="Report only, no writes")
    args = parser.parse_args()

    if args.clients:
        clients = [c.strip() for c in args.clients.split(",")]
    else:
        # All clients with learning articles
        clients = []
        for status_dir in ["current", "former"]:
            base = WIKI_DIR / "clients" / status_dir
            if base.exists():
                for d in sorted(base.iterdir()):
                    if d.is_dir():
                        clients.append(d.name)

    result = run_extraction(clients, dry_run=args.dry_run)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
