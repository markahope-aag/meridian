#!/usr/bin/env python3
"""
classify-engineering-fragments.py — Assign engineering topics to ingested commit fragments.

Reads fragments from capture/external/commits/<project>/*.md, uses Haiku
to assign one primary topic from engineering-topics.yaml (with optional
secondary topics), updates the fragment's frontmatter, and moves it to
wiki/engineering/<topic>/.

Batched 10 commits per API call for efficiency. Unclassified fragments
(no good topic match) stay in capture/ for manual review.

Usage:
    python scripts/classify-engineering-fragments.py --dry-run
    python scripts/classify-engineering-fragments.py --project meridian
    python scripts/classify-engineering-fragments.py          # full run
    python scripts/classify-engineering-fragments.py --batch-size 5
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import anthropic
import yaml

# MERIDIAN_ROOT env var points at the runtime data root (Syncthing vault
# on Mark's local machine, /meridian on the VM). Registries live in the
# git repo, output lives under MERIDIAN_ROOT.
SCRIPT_ROOT = Path(__file__).resolve().parent.parent
MERIDIAN_ROOT = Path(os.environ.get("MERIDIAN_ROOT", SCRIPT_ROOT))

CAPTURE_DIR = MERIDIAN_ROOT / "capture" / "external" / "commits"
WIKI_ENG_DIR = MERIDIAN_ROOT / "wiki" / "engineering"
TOPICS_YAML = SCRIPT_ROOT / "engineering-topics.yaml"
PROJECTS_YAML = SCRIPT_ROOT / "projects.yaml"
ERROR_LOG = MERIDIAN_ROOT / "outputs" / "engineering-classification-errors.log"

HAIKU_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_BATCH_SIZE = 10


@dataclass
class Fragment:
    path: Path
    project: str
    commit_sha: str
    short_sha: str
    subject: str
    body: str
    frontmatter: dict

    # Filled in by classifier
    primary_topic: str = ""
    secondary_topics: list[str] = field(default_factory=list)
    confidence: str = ""
    rationale: str = ""


def load_topics() -> list[dict]:
    with open(TOPICS_YAML) as f:
        data = yaml.safe_load(f) or {}
    return data.get("topics", [])


def load_projects() -> dict[str, dict]:
    with open(PROJECTS_YAML) as f:
        data = yaml.safe_load(f) or {}
    return {p["slug"]: p for p in data.get("projects", [])}


def parse_fragment(path: Path) -> Fragment | None:
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.startswith("---\n"):
        return None
    try:
        end = text.index("\n---\n", 4)
    except ValueError:
        return None
    fm_text = text[4:end]
    body_text = text[end + 5 :]
    try:
        fm = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError:
        return None

    # Extract commit message from the body — it's in a ``` fenced block
    # under "## Commit message"
    commit_msg = ""
    msg_match = re.search(r"## Commit message\s*\n+```\s*\n(.*?)\n```", body_text, re.DOTALL)
    if msg_match:
        commit_msg = msg_match.group(1).strip()

    subject = fm.get("title", "")
    # Strip the subject line from the message if present
    if commit_msg.startswith(subject):
        body_of_msg = commit_msg[len(subject) :].strip()
    else:
        body_of_msg = commit_msg

    return Fragment(
        path=path,
        project=fm.get("source_project", ""),
        commit_sha=fm.get("commit_sha", ""),
        short_sha=fm.get("commit_short_sha", ""),
        subject=subject,
        body=body_of_msg,
        frontmatter=fm,
    )


def build_classification_prompt(topics: list[dict]) -> str:
    topic_lines = []
    for t in topics:
        aliases = ", ".join(t.get("aliases", []))
        alias_part = f" (aliases: {aliases})" if aliases else ""
        topic_lines.append(f"- **{t['slug']}** — {t['name']}{alias_part}")
    topics_block = "\n".join(topic_lines)

    return f"""You are a topic classifier for engineering commit fragments.

Your job: read each commit (subject + body + project stack) and assign it
to the single most relevant topic from this registry:

{topics_block}

## Rules

1. **Return exactly one primary_topic per commit.** Pick the topic that
   best captures the *technical lesson* in the commit — not the project
   it happened in, not the feature it implements, but what someone
   learning that technology would get out of reading this commit.

2. **Use "unclassified" when nothing fits.** Genuine misses (pure business
   logic, generic refactors, commits about tooling outside the registry)
   should return "unclassified" — don't force a bad match. Better to
   leave 30% unclassified than to pollute the wrong topic.

3. **Add up to 2 secondary_topics** when the commit genuinely touches
   multiple areas (e.g. a Supabase RLS fix that involves a Next.js
   route handler could be primary: supabase, secondary: [nextjs]).

4. **confidence**: high | medium | low
   - high: unambiguous — commit message explicitly names the topic tech
   - medium: clear inference from context (libraries, error messages)
   - low: best guess, could reasonably go elsewhere

5. **Be brief with rationale** — one short phrase per commit, not a
   paragraph. Something like "mentions @supabase/ssr and
   exchangeCodeForSession" or "Clerk middleware config change."

## Worked examples

Commit: "Fix invite: use server-side route handler instead of client page"
Body: "@supabase/ssr's browser client auto-detects ?code= params..."
Project: client-brain [typescript, nextjs, supabase, vercel, anthropic-api]

→ primary: supabase, secondary: [nextjs], confidence: high,
  rationale: "mentions @supabase/ssr and exchangeCodeForSession"

Commit: "fix: use admin client for invitation endpoints to bypass RLS"
Body: ""
Project: asymxray [typescript, nextjs, anthropic-api, google-generative-ai]

→ primary: supabase, secondary: [], confidence: high,
  rationale: "RLS + admin client is Supabase pattern"

Commit: "Update SESSION_NOTES.md with Session 24 summary"
Body: ""
Project: labelcheck [typescript, nextjs, clerk, anthropic-api]

→ primary: unclassified, secondary: [], confidence: high,
  rationale: "notes update, not a technical lesson"

Commit: "Add personalized celebration toast when vendor status changes"
Body: ""
Project: eydn-app [typescript, nextjs, clerk, supabase, anthropic-api]

→ primary: react-patterns, secondary: [], confidence: medium,
  rationale: "UI state feedback pattern, React-adjacent"

## Output format

Return a JSON array, one object per commit in the input order:

```json
[
  {{
    "commit_sha": "78a7e85...",
    "primary_topic": "supabase",
    "secondary_topics": ["nextjs"],
    "confidence": "high",
    "rationale": "mentions @supabase/ssr"
  }},
  ...
]
```

No prose outside the JSON. No code fences around it. Just the JSON array.
"""


def format_batch_user_message(fragments: list[Fragment], projects: dict) -> str:
    lines = ["## Classify the following commits\n"]
    for i, frag in enumerate(fragments, 1):
        proj = projects.get(frag.project, {})
        stack = ", ".join(proj.get("stack", []))
        body_snippet = frag.body[:600] if frag.body else "(no body)"
        lines.append(f"### Commit {i}")
        lines.append(f"commit_sha: {frag.commit_sha}")
        lines.append(f"project: {frag.project} [{stack}]")
        lines.append(f"subject: {frag.subject}")
        lines.append(f"body: {body_snippet}")
        lines.append("")
    return "\n".join(lines)


def classify_batch(client: anthropic.Anthropic, system_prompt: str,
                   fragments: list[Fragment], projects: dict) -> list[dict]:
    user_msg = format_batch_user_message(fragments, projects)
    response = client.messages.create(
        model=HAIKU_MODEL,
        max_tokens=2000,
        system=system_prompt,
        messages=[{"role": "user", "content": user_msg}],
    )
    text = response.content[0].text.strip()
    # Strip optional code fences
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    # Find the first JSON array
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        raise ValueError(f"no JSON array in response: {text[:200]}")
    return json.loads(match.group(0))


def update_fragment_file(frag: Fragment, new_topic: str,
                         secondary_topics: list[str],
                         confidence: str, rationale: str) -> str:
    """Rewrite the fragment with updated frontmatter. Returns new text."""
    text = frag.path.read_text(encoding="utf-8")
    # Update or insert topic_slug
    fm_end = text.index("\n---\n", 4)
    fm_text = text[4:fm_end]
    body_text = text[fm_end + 5 :]

    fm = yaml.safe_load(fm_text) or {}
    fm["topic_slug"] = new_topic
    if secondary_topics:
        fm["secondary_topics"] = secondary_topics
    fm["classification_confidence"] = confidence
    fm["classification_rationale"] = rationale

    new_fm = yaml.safe_dump(fm, sort_keys=False, default_flow_style=False, allow_unicode=True).strip()
    return f"---\n{new_fm}\n---\n{body_text}"


def move_to_wiki(frag: Fragment, topic: str, dry_run: bool) -> Path:
    """Move a classified fragment from capture/ to wiki/engineering/<topic>/."""
    dest_dir = WIKI_ENG_DIR / topic
    filename = f"{frag.project}-{frag.short_sha}-{frag.path.stem.split('-', 1)[-1]}.md"
    # The original filename was <short_sha>-<subject-slug>.md;
    # we prefix project to avoid collisions across projects.
    original_stem = frag.path.stem  # e.g. "78a7e85-fix-invite"
    filename = f"{frag.project}-{original_stem}.md"
    dest_path = dest_dir / filename
    if dry_run:
        return dest_path
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.move(str(frag.path), str(dest_path))
    return dest_path


def log_error(msg: str) -> None:
    ERROR_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(ERROR_LOG, "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')}  {msg}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Classify engineering commit fragments by topic")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show classifications without modifying files")
    parser.add_argument("--project", help="Limit to one project slug")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--limit", type=int, help="Process at most N fragments (useful for spot testing)")
    args = parser.parse_args()

    topics = load_topics()
    valid_slugs = {t["slug"] for t in topics} | {"unclassified"}
    projects = load_projects()
    system_prompt = build_classification_prompt(topics)

    # Gather all fragments
    fragments: list[Fragment] = []
    if args.project:
        scan_dirs = [CAPTURE_DIR / args.project]
    else:
        scan_dirs = [d for d in CAPTURE_DIR.iterdir() if d.is_dir()] if CAPTURE_DIR.exists() else []

    for d in scan_dirs:
        if not d.exists():
            continue
        for f in sorted(d.glob("*.md")):
            frag = parse_fragment(f)
            if frag is None:
                log_error(f"failed to parse: {f}")
                continue
            fragments.append(frag)

    if args.limit:
        fragments = fragments[: args.limit]

    if not fragments:
        print("No fragments found to classify.", file=sys.stderr)
        sys.exit(0)

    print(f"Classifying {len(fragments)} fragments in batches of {args.batch_size}...", file=sys.stderr)
    print(f"Model: {HAIKU_MODEL}", file=sys.stderr)
    if args.dry_run:
        print("(DRY RUN — no file changes)", file=sys.stderr)

    client = anthropic.Anthropic()
    total_in_tokens = 0
    total_out_tokens = 0
    classified_count = 0
    unclassified_count = 0
    error_count = 0
    topic_counts: dict[str, int] = {}

    t0 = time.time()
    for batch_start in range(0, len(fragments), args.batch_size):
        batch = fragments[batch_start : batch_start + args.batch_size]
        try:
            results = classify_batch(client, system_prompt, batch, projects)
        except Exception as e:
            log_error(f"batch starting at {batch_start} failed: {e}")
            error_count += len(batch)
            print(f"  batch {batch_start}-{batch_start + len(batch)}: ERROR ({e})", file=sys.stderr)
            continue

        # Match results to fragments by commit_sha
        result_by_sha = {r.get("commit_sha", ""): r for r in results}
        for frag in batch:
            r = result_by_sha.get(frag.commit_sha)
            if r is None:
                # fall back to positional match
                idx = batch.index(frag)
                if idx < len(results):
                    r = results[idx]
            if r is None:
                log_error(f"no result for {frag.commit_sha}")
                error_count += 1
                continue

            primary = r.get("primary_topic", "unclassified")
            if primary not in valid_slugs:
                log_error(f"invalid topic '{primary}' for {frag.commit_sha}, falling back to unclassified")
                primary = "unclassified"

            frag.primary_topic = primary
            frag.secondary_topics = [
                s for s in r.get("secondary_topics", []) if s in valid_slugs and s != "unclassified"
            ]
            frag.confidence = r.get("confidence", "medium")
            frag.rationale = r.get("rationale", "")

            topic_counts[primary] = topic_counts.get(primary, 0) + 1
            if primary == "unclassified":
                unclassified_count += 1
            else:
                classified_count += 1

            if not args.dry_run:
                try:
                    new_text = update_fragment_file(
                        frag, primary, frag.secondary_topics, frag.confidence, frag.rationale
                    )
                    # If classified, move to wiki; if unclassified, leave in capture/ with updated frontmatter
                    if primary == "unclassified":
                        frag.path.write_text(new_text, encoding="utf-8")
                    else:
                        # Write updated content, then move
                        frag.path.write_text(new_text, encoding="utf-8")
                        move_to_wiki(frag, primary, dry_run=False)
                except Exception as e:
                    log_error(f"failed to update/move {frag.path}: {e}")
                    error_count += 1

        total_in_tokens += getattr(
            getattr(client, "_last_response", None), "usage", None
        ).input_tokens if False else 0
        # The usage is on the response, not tracked across calls here.
        # Simpler: we'll just estimate cost at the end.

        elapsed = time.time() - t0
        processed = batch_start + len(batch)
        print(f"  {processed}/{len(fragments)} processed ({elapsed:.1f}s elapsed)", file=sys.stderr)

    elapsed = time.time() - t0
    print(file=sys.stderr)
    print("=" * 70, file=sys.stderr)
    print(f"  Classified {classified_count} → moved to wiki/engineering/", file=sys.stderr)
    print(f"  Unclassified {unclassified_count} → left in capture/", file=sys.stderr)
    print(f"  Errors {error_count}", file=sys.stderr)
    print(f"  Elapsed: {elapsed:.1f}s", file=sys.stderr)
    print("=" * 70, file=sys.stderr)
    print(file=sys.stderr)
    print("Per-topic distribution:", file=sys.stderr)
    for topic, count in sorted(topic_counts.items(), key=lambda x: -x[1]):
        print(f"  {count:>5}  {topic}", file=sys.stderr)


if __name__ == "__main__":
    main()
