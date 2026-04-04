#!/usr/bin/env python3
"""Meridian Q&A Agent — answer questions by researching the wiki.

Usage:
    python agents/qa_agent.py --question "What is the LLM wiki pattern?"

Searches the wiki for relevant articles, sends them to the LLM with the
question, and returns a synthesized answer.

Output: the answer as markdown to stdout.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

import anthropic
import yaml


ROOT = Path(__file__).parent.parent
WIKI_DIR = ROOT / "wiki"
PROMPTS_DIR = ROOT / "prompts"


def load_config() -> dict:
    with open(ROOT / "config.yaml") as f:
        return yaml.safe_load(f)


def load_prompt() -> str:
    return (PROMPTS_DIR / "qa_agent.md").read_text(encoding="utf-8")


def load_index() -> str:
    path = WIKI_DIR / "_index.md"
    return path.read_text(encoding="utf-8") if path.exists() else "_No index yet._"


def search_wiki(query: str, max_results: int = 10) -> list[dict]:
    """Search wiki files for content matching the query terms."""
    terms = [t.lower() for t in query.split() if len(t) > 2]
    if not terms:
        terms = [query.lower()]

    scored = []
    if not WIKI_DIR.exists():
        return []

    for md_file in WIKI_DIR.rglob("*.md"):
        if md_file.name in ("_backlinks.md", "log.md", "home.md"):
            continue
        try:
            content = md_file.read_text(encoding="utf-8", errors="replace")
            content_lower = content.lower()

            # Score by number of query term matches
            score = sum(content_lower.count(term) for term in terms)
            if score > 0:
                # Extract title from frontmatter
                title = md_file.stem
                title_match = re.search(r'^title:\s*["\']?(.+?)["\']?\s*$', content, re.MULTILINE)
                if title_match:
                    title = title_match.group(1)

                # Get body without frontmatter
                body = content
                if content.startswith("---"):
                    parts = content.split("---", 2)
                    if len(parts) >= 3:
                        body = parts[2].strip()

                rel_path = str(md_file.relative_to(ROOT))
                scored.append({
                    "path": rel_path,
                    "title": title,
                    "score": score,
                    "content": body[:3000],  # cap per article
                })
        except Exception:
            continue

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:max_results]


def ask_llm(client: anthropic.Anthropic, question: str, index_md: str,
            articles: list[dict], config: dict) -> str:
    """Send the question and relevant articles to the LLM."""
    system_prompt = load_prompt()

    # Build context from found articles
    if articles:
        article_text = "\n\n".join(
            f"### {a['path']}\n**Title:** {a['title']}\n\n{a['content']}"
            for a in articles
        )
    else:
        article_text = "_No relevant wiki articles found._"

    user_content = (
        f"## Question\n\n{question}\n\n"
        f"## wiki/_index.md\n\n{index_md}\n\n"
        f"## Relevant Wiki Articles\n\n{article_text}"
    )

    response = client.messages.create(
        model=config["llm"]["model"],
        max_tokens=4096,
        temperature=0.3,
        system=system_prompt,
        messages=[{
            "role": "user",
            "content": user_content,
        }],
    )

    return response.content[0].text


def main():
    parser = argparse.ArgumentParser(description="Meridian Q&A Agent")
    parser.add_argument("--question", required=True, help="Question to answer")
    args = parser.parse_args()

    config = load_config()
    client = anthropic.Anthropic()
    index_md = load_index()

    print(f"Searching wiki for: {args.question}", file=sys.stderr)
    articles = search_wiki(args.question)
    print(f"Found {len(articles)} relevant articles", file=sys.stderr)

    answer = ask_llm(client, args.question, index_md, articles, config)
    print(answer)


if __name__ == "__main__":
    main()
