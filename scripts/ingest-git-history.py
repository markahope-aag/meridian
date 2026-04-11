#!/usr/bin/env python3
"""
ingest-git-history.py — Extract meaningful git commits as Meridian fragments.

Reads projects.yaml to get the list of repos, walks each repo's git log,
filters out noise (wip/typo/merge commits, short messages), and writes
one fragment per meaningful commit to
capture/external/commits/<project-slug>/<short-sha>-<slug>.md.

Fragments are NOT topic-classified at ingest time. Topic assignment is
a separate later step (LLM-based classifier or manual review). This
keeps the ingestor deterministic and easy to re-run.

Usage:
    python scripts/ingest-git-history.py --dry-run
    python scripts/ingest-git-history.py --project meridian
    python scripts/ingest-git-history.py --since 2025-01-01
    python scripts/ingest-git-history.py          # write mode, all projects
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import yaml

# Resolve MERIDIAN_ROOT the same way the web app does: env var wins,
# falls back to the git repo's root (script parent-parent). This matters
# on Mark's local machine where the Syncthing vault lives at a
# different path than the git repo — set MERIDIAN_ROOT to the vault
# so ingestion output goes straight to the path Syncthing replicates.
SCRIPT_ROOT = Path(__file__).resolve().parent.parent
MERIDIAN_ROOT = Path(os.environ.get("MERIDIAN_ROOT", SCRIPT_ROOT))

# projects.yaml is always in the git repo (it's tracked code), even if
# output goes to the vault via MERIDIAN_ROOT. Read from SCRIPT_ROOT.
PROJECTS_YAML = SCRIPT_ROOT / "projects.yaml"
CAPTURE_DIR = MERIDIAN_ROOT / "capture" / "external" / "commits"

# Minimum commit message length (subject + body) to be considered signal
MIN_MESSAGE_LENGTH = 80

# Regex patterns that flag a commit as noise (case-insensitive, on subject)
NOISE_PATTERNS = [
    re.compile(r"^wip\b", re.IGNORECASE),
    re.compile(r"^fix\s+typo", re.IGNORECASE),
    re.compile(r"^typo", re.IGNORECASE),
    re.compile(r"^merge\s+branch", re.IGNORECASE),
    re.compile(r"^merge\s+pull\s+request", re.IGNORECASE),
    re.compile(r"^merge\s+remote", re.IGNORECASE),
    re.compile(r"^bump\s+version", re.IGNORECASE),
    re.compile(r"^version\s+bump", re.IGNORECASE),
    re.compile(r"^update\s+(readme|deps|dependencies|packages|package-lock)\b", re.IGNORECASE),
    re.compile(r"^initial\s+commit\b", re.IGNORECASE),
    re.compile(r"^first\s+commit\b", re.IGNORECASE),
    re.compile(r"^\.?$"),
    re.compile(r"^format\b", re.IGNORECASE),
    re.compile(r"^lint\b", re.IGNORECASE),
    re.compile(r"^prettier\b", re.IGNORECASE),
]


@dataclass
class Commit:
    sha: str
    short_sha: str
    author: str
    author_email: str
    date_iso: str
    subject: str
    body: str
    files_changed: int
    insertions: int
    deletions: int

    @property
    def full_message(self) -> str:
        if self.body:
            return f"{self.subject}\n\n{self.body}"
        return self.subject


@dataclass
class ProjectStats:
    slug: str
    total_commits: int = 0
    kept: int = 0
    filtered_short: int = 0
    filtered_noise: int = 0
    filter_reasons: dict = field(default_factory=dict)
    samples: list[str] = field(default_factory=list)
    error: str | None = None


def load_projects() -> list[dict]:
    if not PROJECTS_YAML.exists():
        print(f"ERROR: {PROJECTS_YAML} not found", file=sys.stderr)
        sys.exit(1)
    with open(PROJECTS_YAML) as f:
        data = yaml.safe_load(f) or {}
    return data.get("projects", [])


def run_git(repo_path: Path, args: list[str]) -> str:
    """Run a git command in repo_path and return stdout. Raises on error."""
    result = subprocess.run(
        ["git"] + args,
        cwd=str(repo_path),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed in {repo_path}: {result.stderr.strip()}"
        )
    return result.stdout


def repo_is_git(repo_path: Path) -> bool:
    return (repo_path / ".git").exists() or (
        repo_path.exists()
        and subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=str(repo_path),
            capture_output=True,
        ).returncode
        == 0
    )


def iter_commits(repo_path: Path, since: str | None = None) -> Iterable[Commit]:
    """Yield Commit records from a repo, newest-first."""
    # Use a null-byte record separator to survive commit messages with newlines.
    fmt = "%x00%H%x1f%h%x1f%an%x1f%ae%x1f%aI%x1f%s%x1f%b"
    args = ["log", f"--format={fmt}"]
    if since:
        args.append(f"--since={since}")
    try:
        output = run_git(repo_path, args)
    except RuntimeError as e:
        raise RuntimeError(str(e))

    # Records are separated by \x00
    records = output.split("\x00")
    for rec in records:
        if not rec.strip():
            continue
        fields = rec.split("\x1f")
        if len(fields) < 7:
            continue
        sha, short_sha, author, email, date_iso, subject, body = fields[:7]
        yield Commit(
            sha=sha.strip(),
            short_sha=short_sha.strip(),
            author=author.strip(),
            author_email=email.strip(),
            date_iso=date_iso.strip(),
            subject=subject.strip(),
            body=body.strip(),
            files_changed=0,
            insertions=0,
            deletions=0,
        )


def get_commit_stats(repo_path: Path, sha: str) -> tuple[int, int, int]:
    """Return (files_changed, insertions, deletions) for a commit."""
    try:
        out = run_git(repo_path, ["show", "--stat", "--format=", sha])
    except RuntimeError:
        return (0, 0, 0)
    # Last non-empty line like: " 5 files changed, 123 insertions(+), 45 deletions(-)"
    files = ins = dels = 0
    lines = [l for l in out.strip().splitlines() if l.strip()]
    if not lines:
        return (0, 0, 0)
    summary = lines[-1]
    m = re.search(r"(\d+)\s+files?\s+changed", summary)
    if m:
        files = int(m.group(1))
    m = re.search(r"(\d+)\s+insertions?\(\+\)", summary)
    if m:
        ins = int(m.group(1))
    m = re.search(r"(\d+)\s+deletions?\(-\)", summary)
    if m:
        dels = int(m.group(1))
    return (files, ins, dels)


def classify_filter(commit: Commit) -> str | None:
    """Return a filter reason if commit should be filtered, else None."""
    if len(commit.full_message) < MIN_MESSAGE_LENGTH:
        return "too-short"
    for pat in NOISE_PATTERNS:
        if pat.search(commit.subject):
            return f"noise:{pat.pattern[:30]}"
    return None


def slugify(text: str, max_len: int = 50) -> str:
    """Produce a filesystem-safe slug from a commit subject."""
    # Strip conventional-commit prefixes
    text = re.sub(r"^(feat|fix|chore|docs|refactor|test|perf|style|ci|build)(\([^)]*\))?:\s*", "", text, flags=re.IGNORECASE)
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"\s+", "-", text).strip("-")
    if len(text) > max_len:
        text = text[:max_len].rstrip("-")
    return text or "commit"


def format_fragment(project_slug: str, project_name: str, project_description: str,
                    commit: Commit) -> str:
    """Render a commit as a Layer 2 fragment with full frontmatter."""
    title = commit.subject.strip()
    # Escape quotes in title for YAML safety
    title_yaml = title.replace('"', '\\"')
    body_lines = [
        "---",
        f'title: "{title_yaml}"',
        "layer: 2",
        "namespace: engineering",
        "source_type: internal-commit",
        "source_origin: git",
        f"source_project: {project_slug}",
        f"source_date: {commit.date_iso[:10]}",
        f'source_author: "{commit.author}"',
        f"source_author_email: {commit.author_email}",
        f"commit_sha: {commit.sha}",
        f"commit_short_sha: {commit.short_sha}",
        f"files_changed: {commit.files_changed}",
        f"insertions: {commit.insertions}",
        f"deletions: {commit.deletions}",
        "topic_slug: unclassified",  # filled in by later classifier pass
        "---",
        "",
        f"# {title}",
        "",
        f"**Project:** {project_name} (`{project_slug}`)  ",
        f"**Date:** {commit.date_iso[:10]}  ",
        f"**Author:** {commit.author}  ",
        f"**Commit:** `{commit.short_sha}`  ",
        f"**Scope:** {commit.files_changed} files, +{commit.insertions}/-{commit.deletions}  ",
        "",
        "## Commit message",
        "",
        "```",
        commit.full_message,
        "```",
        "",
        f"## Project context",
        "",
        project_description,
        "",
    ]
    return "\n".join(body_lines)


def process_project(project: dict, since: str | None, dry_run: bool,
                    capture_stats: bool) -> ProjectStats:
    slug = project.get("slug", "")
    name = project.get("name", slug)
    description = project.get("description", "")
    repo_path = Path(project.get("repo_path", ""))

    stats = ProjectStats(slug=slug)

    if not repo_path.exists():
        stats.error = f"repo_path does not exist: {repo_path}"
        return stats
    if not repo_is_git(repo_path):
        stats.error = f"not a git repository: {repo_path}"
        return stats

    out_dir = CAPTURE_DIR / slug
    if not dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)

    try:
        commits = list(iter_commits(repo_path, since=since))
    except RuntimeError as e:
        stats.error = str(e)
        return stats

    stats.total_commits = len(commits)

    for commit in commits:
        reason = classify_filter(commit)
        if reason:
            if reason == "too-short":
                stats.filtered_short += 1
            else:
                stats.filtered_noise += 1
            stats.filter_reasons[reason] = stats.filter_reasons.get(reason, 0) + 1
            continue

        stats.kept += 1
        if len(stats.samples) < 3:
            stats.samples.append(f"{commit.short_sha}  {commit.subject}")

        if dry_run:
            continue

        # Optionally fetch diff stats (expensive — only when writing)
        if capture_stats:
            commit.files_changed, commit.insertions, commit.deletions = (
                get_commit_stats(repo_path, commit.sha)
            )

        filename = f"{commit.short_sha}-{slugify(commit.subject)}.md"
        out_path = out_dir / filename
        if out_path.exists():
            # Incremental: skip existing
            continue
        fragment = format_fragment(slug, name, description, commit)
        out_path.write_text(fragment, encoding="utf-8")

    return stats


def print_report(all_stats: list[ProjectStats], dry_run: bool) -> None:
    print()
    print("=" * 70)
    mode = "DRY RUN — no files written" if dry_run else "WRITE MODE"
    print(f"  {mode}")
    print("=" * 70)
    print()
    print(f"{'Project':<22} {'Total':>7} {'Kept':>7} {'Short':>7} {'Noise':>7}  Error")
    print("-" * 70)
    total_total = total_kept = total_short = total_noise = 0
    for s in all_stats:
        err = s.error or ""
        print(f"{s.slug:<22} {s.total_commits:>7} {s.kept:>7} {s.filtered_short:>7} {s.filtered_noise:>7}  {err}")
        total_total += s.total_commits
        total_kept += s.kept
        total_short += s.filtered_short
        total_noise += s.filtered_noise
    print("-" * 70)
    print(f"{'TOTAL':<22} {total_total:>7} {total_kept:>7} {total_short:>7} {total_noise:>7}")
    print()
    print("Sample kept commits per project:")
    for s in all_stats:
        if s.samples:
            print(f"\n  [{s.slug}]")
            for sample in s.samples:
                print(f"    {sample}")
    print()
    if any(s.filter_reasons for s in all_stats):
        print("Top filter reasons (aggregated):")
        agg = {}
        for s in all_stats:
            for k, v in s.filter_reasons.items():
                agg[k] = agg.get(k, 0) + v
        for reason, count in sorted(agg.items(), key=lambda x: -x[1])[:10]:
            print(f"  {count:>5}  {reason}")
        print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest git commit history as Meridian fragments")
    parser.add_argument("--dry-run", action="store_true", help="Report stats without writing files")
    parser.add_argument("--project", help="Limit to a single project slug")
    parser.add_argument("--since", help="Only commits since this date (YYYY-MM-DD)")
    parser.add_argument("--no-stats", action="store_true",
                        help="Skip per-commit diff stats (faster, but fragments lack file counts)")
    args = parser.parse_args()

    projects = load_projects()
    if args.project:
        projects = [p for p in projects if p.get("slug") == args.project]
        if not projects:
            print(f"No project with slug '{args.project}' found in projects.yaml", file=sys.stderr)
            sys.exit(1)

    # Skip archived projects by default — archived repos rarely yield new learnings
    active_projects = [p for p in projects if p.get("status") != "archived"]
    skipped_archived = len(projects) - len(active_projects)
    if skipped_archived:
        print(f"(skipping {skipped_archived} archived project(s))", file=sys.stderr)

    all_stats = []
    for project in active_projects:
        slug = project.get("slug", "?")
        print(f"Processing {slug}...", file=sys.stderr)
        stats = process_project(
            project,
            since=args.since,
            dry_run=args.dry_run,
            capture_stats=not args.no_stats,
        )
        all_stats.append(stats)

    print_report(all_stats, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
