#!/usr/bin/env python3
"""Classify each Meridian client into one of the industries in
industries.yaml, using Haiku as the judge.

Workflow:
  1. Load the industry registry (name, slug, description for each).
  2. Parse clients.yaml into a list of clients.
  3. For each client, find its wiki/clients/<status>/<slug>/_index.md
     and extract a short body excerpt (~2000 chars).
  4. Call Haiku with the industry list + client info and ask for a
     single best-fit classification with a confidence tag.
  5. Surgically edit clients.yaml — insert `    industry: <slug>` on
     the line directly after each client's `slug:` line, or replace
     an existing industry line if present.
  6. Report a per-client summary to stdout plus a sorted leaderboard
     of low-confidence picks that should get a human eye.

Usage:
    python scripts/classify-clients-by-industry.py               # write clients.yaml + report
    python scripts/classify-clients-by-industry.py --dry-run     # report only, no file changes
    python scripts/classify-clients-by-industry.py --slug aviary # single client by slug

This is deterministic enough for a mass classification (temperature=0,
structured JSON) but you should still eyeball the low-confidence rows
before trusting them for downstream cross-filing.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import anthropic
import yaml


ROOT = Path(__file__).resolve().parent.parent
CLIENTS_YAML = ROOT / "clients.yaml"
INDUSTRIES_YAML = ROOT / "industries.yaml"
WIKI_CLIENTS = ROOT / "wiki" / "clients"
JUDGE_MODEL = "claude-haiku-4-5-20251001"
INDEX_EXCERPT_BYTES = 2500

# Manual overrides that beat the classifier. The Haiku judge gets
# confused by keyword-heavy index pages (e.g. an "asbestos services"
# client tagged as healthcare, or a therapeutic horsemanship
# nonprofit tagged b2b because its index has "B2B Services: 7
# insights" listed as a synthesis sub-header). These are corrections
# we already know are right — apply them without asking the LLM.
MANUAL_OVERRIDES: dict[str, tuple[str, str]] = {
    "ahs":             ("construction-home-services", "Madison Asbestos Services — asbestos remediation, not healthcare"),
    "seamless":        ("construction-home-services", "Seamless Building Solutions — construction, not b2b-services"),
    "new-dawn-shine":  ("healthcare",                  "A New Dawn / Shine — therapy services"),
    "three-gaits":     ("nonprofit",                   "Therapeutic horsemanship nonprofit for veterans and people with disabilities"),
    "agility-recovery": ("saas",                       "Business continuity / disaster recovery SaaS platform"),
}

# Clients that should NOT get an industry tag at all. _internal is a
# meta-slug Asymmetric uses for its own ops/billing/sprint entries —
# not a real client, so classifying it under any industry would be
# noise.
EXCLUDE_SLUGS: set[str] = {"_internal"}

SYSTEM_PROMPT = """You are classifying a marketing agency's clients into industry verticals.

You will be given:
1. A list of industries (slug, name, and one-sentence scope).
2. A single client's name, optional website, optional aliases, and an
   excerpt from their internal index page describing the work the
   agency has done with them.

Pick EXACTLY ONE industry slug from the provided list. Choose the
best primary classification even if the client could plausibly
fit more than one. Do not invent new industries.

Assign a confidence level:
- "high"   = the client is unambiguously in this industry based on
             their name, website, or description.
- "medium" = the industry is the best match but there are plausible
             alternatives; the excerpt provides reasonable evidence.
- "low"    = the classification is a guess from limited information
             (e.g. the client has no _index.md or the content is
             ambiguous). A human should review.

Return STRICTLY valid JSON with this exact shape, no prose before or after:

    {"industry": "<slug from the list>", "confidence": "high|medium|low", "reason": "<one concise sentence>"}

Use the industry slug exactly as it appears in the list. Reason must
reference specific evidence from the client info when available."""


def load_industries() -> tuple[list[dict], set[str]]:
    data = yaml.safe_load(INDUSTRIES_YAML.read_text(encoding="utf-8")) or {}
    entries = []
    for item in data.get("industries", []):
        if not isinstance(item, dict):
            continue
        entries.append(
            {
                "slug": item.get("slug", ""),
                "name": item.get("name", ""),
                "description": item.get("description", ""),
            }
        )
    slugs = {e["slug"] for e in entries if e["slug"]}
    return entries, slugs


def load_clients() -> list[dict]:
    data = yaml.safe_load(CLIENTS_YAML.read_text(encoding="utf-8")) or {}
    return [c for c in data.get("clients", []) if isinstance(c, dict) and c.get("slug")]


def find_client_index(slug: str) -> Path | None:
    for status in ("current", "former", "prospects"):
        p = WIKI_CLIENTS / status / slug / "_index.md"
        if p.exists():
            return p
    return None


def read_excerpt(path: Path | None) -> str:
    if path is None:
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    # Strip frontmatter
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            text = parts[2]
    text = re.sub(r"\s+", " ", text).strip()
    return text[:INDEX_EXCERPT_BYTES]


def build_industry_block(industries: list[dict]) -> str:
    lines = []
    for e in industries:
        lines.append(f"- {e['slug']} ({e['name']}): {e['description']}")
    return "\n".join(lines)


def classify_client(
    client: dict,
    industry_block: str,
    valid_slugs: set[str],
    judge: anthropic.Anthropic,
) -> dict:
    """Return {industry, confidence, reason} for one client."""
    excerpt = read_excerpt(find_client_index(client["slug"]))
    user_content = (
        "## Industries\n\n"
        f"{industry_block}\n\n"
        "## Client\n\n"
        f"Name: {client.get('name', client['slug'])}\n"
        f"Slug: {client['slug']}\n"
        f"Status: {client.get('status', 'unknown')}\n"
        f"Website: {client.get('website', '(none)')}\n"
        f"Aliases: {', '.join(client.get('aliases') or []) or '(none)'}\n\n"
        "## Excerpt from the client's index page\n\n"
        f"{excerpt or '(no _index.md found — classify from name, website, and aliases only, and use low confidence)'}"
    )
    response = judge.messages.create(
        model=JUDGE_MODEL,
        max_tokens=400,
        temperature=0.0,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    )
    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*\n?", "", raw)
        raw = re.sub(r"\n?```\s*$", "", raw)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {
            "industry": "",
            "confidence": "low",
            "reason": f"failed to parse judge output: {raw[:200]}",
        }

    industry = parsed.get("industry", "").strip()
    if industry not in valid_slugs:
        parsed["confidence"] = "low"
        parsed["reason"] = (
            f"judge picked '{industry}' which is not in the registry; needs manual review. "
            f"Original reason: {parsed.get('reason', '')}"
        )
        parsed["industry"] = ""
    return {
        "industry": parsed.get("industry", ""),
        "confidence": parsed.get("confidence", "low"),
        "reason": parsed.get("reason", ""),
    }


def write_industry_into_yaml(
    picks: dict[str, dict],
) -> tuple[int, int]:
    """Surgically insert `    industry: <slug>` after each client's `slug:` line.

    Line-based edit preserves comments, indentation, and aliases. Replaces
    existing industry lines if they're already there (idempotent).

    Returns (inserted, updated) counts.
    """
    text = CLIENTS_YAML.read_text(encoding="utf-8")
    lines = text.splitlines()
    out: list[str] = []
    inserted = 0
    updated = 0

    i = 0
    current_slug: str | None = None
    while i < len(lines):
        line = lines[i]
        m = re.match(r"^(\s*)slug:\s*(\S+)", line)
        if m:
            indent = m.group(1)
            current_slug = m.group(2)
            out.append(line)

            # Look ahead: is there already an industry line for this client?
            # Scan until the next `- name:` or EOF
            j = i + 1
            existing_industry_line: int | None = None
            next_entry_line: int | None = None
            while j < len(lines):
                if re.match(r"^\s*-\s+name:", lines[j]):
                    next_entry_line = j
                    break
                if re.match(rf"^{re.escape(indent)}industry:\s*", lines[j]):
                    existing_industry_line = j
                    break
                j += 1

            pick = picks.get(current_slug) or {}
            industry_slug = pick.get("industry", "")
            confidence = pick.get("confidence", "")
            if not industry_slug:
                i += 1
                current_slug = None
                continue

            comment = f"  # classifier: {confidence}"
            new_line = f"{indent}industry: {industry_slug}{comment}"

            if existing_industry_line is not None:
                # Replace existing line (scan forward and skip the old line later)
                lines[existing_industry_line] = new_line
                updated += 1
            else:
                # Insert just after the slug line
                out.append(new_line)
                inserted += 1

            i += 1
            current_slug = None
            continue

        out.append(line)
        i += 1

    final = "\n".join(out)
    if not final.endswith("\n"):
        final += "\n"
    CLIENTS_YAML.write_text(final, encoding="utf-8")
    return inserted, updated


def main() -> None:
    parser = argparse.ArgumentParser(description="Classify clients by industry")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--slug", help="Classify only this one client")
    args = parser.parse_args()

    industries, valid_slugs = load_industries()
    if not industries:
        print("FATAL: industries.yaml empty or missing", file=sys.stderr)
        sys.exit(2)

    clients = load_clients()
    if args.slug:
        clients = [c for c in clients if c["slug"] == args.slug]
        if not clients:
            print(f"FATAL: no client with slug '{args.slug}' in clients.yaml", file=sys.stderr)
            sys.exit(2)

    industry_block = build_industry_block(industries)
    judge = anthropic.Anthropic()

    picks: dict[str, dict] = {}
    low_conf: list[tuple[str, str]] = []
    t0 = time.time()
    for i, client in enumerate(clients, 1):
        slug = client["slug"]

        if slug in EXCLUDE_SLUGS:
            print(f"  [{i}/{len(clients)}] _ {slug:<28} → (excluded)")
            continue

        if slug in MANUAL_OVERRIDES:
            industry, reason = MANUAL_OVERRIDES[slug]
            picks[slug] = {"industry": industry, "confidence": "high", "reason": f"[override] {reason}"}
            print(f"  [{i}/{len(clients)}] ! {slug:<28} → {industry:<28} (override) {reason[:90]}")
            continue

        result = classify_client(client, industry_block, valid_slugs, judge)
        picks[slug] = result
        conf = result["confidence"]
        industry = result["industry"] or "(none)"
        reason = result["reason"][:90]
        marker = {"high": "✓", "medium": "~", "low": "?"}.get(conf, "?")
        print(f"  [{i}/{len(clients)}] {marker} {slug:<28} → {industry:<28} ({conf:<6}) {reason}")
        if conf == "low":
            low_conf.append((slug, result["reason"]))
        time.sleep(0.15)

    elapsed = time.time() - t0
    print()
    print(f"Classified {len(clients)} clients in {elapsed:.1f}s")

    if low_conf:
        print()
        print(f"{len(low_conf)} LOW-CONFIDENCE picks to review:")
        for slug, reason in low_conf:
            print(f"  {slug}: {reason[:150]}")

    if args.dry_run:
        print()
        print("Dry run — clients.yaml not modified")
        return

    inserted, updated = write_industry_into_yaml(picks)
    print()
    print(f"clients.yaml: +{inserted} inserted, {updated} updated in place")


if __name__ == "__main__":
    main()
