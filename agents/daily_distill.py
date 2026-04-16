#!/usr/bin/env python3
"""Meridian Daily Distill — review capture/ and promote worthy docs to raw/.

Usage:
    python agents/daily_distill.py                    # review all unprocessed
    python agents/daily_distill.py --file capture/foo.md  # review a specific file
    python agents/daily_distill.py --approve capture/foo.md  # approve a pending promotion
    python agents/daily_distill.py --dry-run           # score without writing

Output: JSON summary of decisions to stdout.
"""

import argparse
import json
import os
import re
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import anthropic
import yaml


ROOT = Path(__file__).parent.parent
CAPTURE_DIR = ROOT / "capture"
RAW_DIR = ROOT / "raw"
PROMPTS_DIR = ROOT / "prompts"
STATE_DIR = ROOT / "state"
# Per-run staging area inside capture/. Files atomically moved here before
# scoring so the ClientBrain sync (which writes the per-client subdirs) can't
# race with us. `.processing/` is excluded from get_unprocessed_files().
PROCESSING_DIR = CAPTURE_DIR / ".processing"
# Quarantine for files that have failed too many runs in a row. Excluded
# from get_unprocessed_files() so they don't keep wedging the pipeline.
# Move files back manually (or via --unquarantine) once the root cause is fixed.
FAILED_DIR = CAPTURE_DIR / ".failed"
# Persistent failure counter. Maps capture-relative paths to {count, last_error,
# last_attempt}. Lives outside capture/ so syncs can't clobber it.
FAILURE_STATE_PATH = STATE_DIR / "distill_failures.json"
# After this many consecutive failures on the same file, quarantine it.
MAX_FAILURES = 3


def load_config() -> dict:
    with open(ROOT / "config.yaml") as f:
        return yaml.safe_load(f)


def load_prompt() -> str:
    return (PROMPTS_DIR / "daily_distill.md").read_text(encoding="utf-8")


def _is_under(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def get_unprocessed_files() -> list[Path]:
    """Find capture/ files that haven't been processed by distill yet.

    Skips both `.processing/` (owned by an in-flight run) and `.failed/`
    (quarantined files that have failed too many times in a row).
    """
    files = []
    for f in sorted(CAPTURE_DIR.rglob("*.md")):
        if _is_under(f, PROCESSING_DIR) or _is_under(f, FAILED_DIR):
            continue
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            continue
        if "distill_status:" not in content:
            files.append(f)
    return files


def load_failures() -> dict:
    """Load the persistent failure-count map. Returns {} on first run."""
    if not FAILURE_STATE_PATH.exists():
        return {}
    try:
        return json.loads(FAILURE_STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_failures(failures: dict) -> None:
    """Write the failure-count map. Best-effort; never raises."""
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        FAILURE_STATE_PATH.write_text(json.dumps(failures, indent=2), encoding="utf-8")
    except OSError as e:
        print(f"save_failures: {e}", file=sys.stderr)


def record_failure(failures: dict, rel_key: str, error: str) -> int:
    """Increment failure count for a file and return the new count."""
    entry = failures.get(rel_key, {"count": 0})
    entry["count"] = int(entry.get("count", 0)) + 1
    entry["last_error"] = error[:500]
    entry["last_attempt"] = datetime.now(timezone.utc).isoformat()
    failures[rel_key] = entry
    return entry["count"]


def clear_failure(failures: dict, rel_key: str) -> None:
    """Drop a file's failure record after a successful run."""
    failures.pop(rel_key, None)


def quarantine_file(staged_path: Path, rel_key: str, error_history: dict) -> Path | None:
    """Move a staged file into `.failed/` with an error sidecar.

    The sidecar (`<name>.error.json`) records the failure history so a future
    investigator can see exactly why the file was quarantined.
    """
    try:
        target = FAILED_DIR / rel_key
        target.parent.mkdir(parents=True, exist_ok=True)
        # If a same-named quarantined file already exists, suffix with timestamp
        if target.exists():
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            target = target.with_name(f"{target.stem}.{stamp}{target.suffix}")
        staged_path.rename(target)
        sidecar = target.with_suffix(target.suffix + ".error.json")
        sidecar.write_text(
            json.dumps({"quarantined_at": datetime.now(timezone.utc).isoformat(),
                        "history": error_history}, indent=2),
            encoding="utf-8",
        )
        return target
    except Exception as e:
        print(f"quarantine_file: failed for {rel_key}: {e}", file=sys.stderr)
        return None


def unquarantine_all() -> int:
    """Move every file in `.failed/` back into capture/. Returns count."""
    if not FAILED_DIR.exists():
        return 0
    moved = 0
    for f in FAILED_DIR.rglob("*.md"):
        try:
            rel = f.relative_to(FAILED_DIR)
            dest = CAPTURE_DIR / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                f.unlink()
            else:
                f.rename(dest)
            sidecar = f.with_suffix(f.suffix + ".error.json")
            if sidecar.exists():
                sidecar.unlink()
            moved += 1
        except Exception as e:
            print(f"unquarantine_all: failed on {f}: {e}", file=sys.stderr)
    return moved


def recover_stranded() -> int:
    """Move any files left in `.processing/` back into capture/.

    Runs at startup. A previous run that crashed (OOM, SIGKILL, container
    restart) may have left files in staging. We restore them to their
    original capture/ paths so the current run picks them up normally.
    Returns the number of files restored.
    """
    if not PROCESSING_DIR.exists():
        return 0
    restored = 0
    for staged in PROCESSING_DIR.rglob("*.md"):
        try:
            rel = staged.relative_to(PROCESSING_DIR)
            # Layout in staging: <run-id>/<original-relative-path>
            # Strip the run-id segment to recover the original location.
            if len(rel.parts) < 2:
                continue
            original = CAPTURE_DIR / Path(*rel.parts[1:])
            original.parent.mkdir(parents=True, exist_ok=True)
            if original.exists():
                # Original slot is occupied (sync recreated it). Drop the
                # stranded copy rather than overwriting newer data.
                staged.unlink()
            else:
                staged.rename(original)
            restored += 1
        except Exception as e:
            print(f"recover_stranded: failed on {staged}: {e}", file=sys.stderr)
    # Best-effort cleanup of empty run-id dirs
    for run_dir in list(PROCESSING_DIR.iterdir()) if PROCESSING_DIR.exists() else []:
        try:
            if run_dir.is_dir() and not any(run_dir.rglob("*")):
                shutil.rmtree(run_dir)
        except Exception:
            pass
    return restored


def claim_file(filepath: Path, run_dir: Path) -> Path | None:
    """Atomically move a capture file into our run's staging dir.

    Returns the new path on success, or None if the file vanished before we
    could claim it (lost the race to a concurrent writer/deleter).
    """
    try:
        rel = filepath.relative_to(CAPTURE_DIR)
    except ValueError:
        return None
    staged = run_dir / rel
    staged.parent.mkdir(parents=True, exist_ok=True)
    try:
        filepath.rename(staged)
    except FileNotFoundError:
        return None
    return staged


def score_document(client: anthropic.Anthropic, content: str, config: dict) -> dict:
    """Send document to LLM for scoring."""
    system_prompt = load_prompt()

    response = client.messages.create(
        model=config["llm"]["model"],
        max_tokens=config["llm"]["max_tokens"],
        temperature=config["llm"]["temperature"],
        system=system_prompt,
        messages=[{
            "role": "user",
            "content": f"Review this document:\n\n{content}"
        }],
    )

    # Parse JSON from response
    text = response.content[0].text
    # Extract JSON if wrapped in markdown code block
    json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if json_match:
        text = json_match.group(1)

    return json.loads(text)


def mark_processed(filepath: Path, decision: dict, error: str | None = None) -> None:
    """Add distill metadata to the capture file's frontmatter.

    When `error` is set, records a `distill_status: error` marker alongside
    the error message so downstream tooling can tell scored items apart from
    items that fell through unscored.
    """
    content = filepath.read_text(encoding="utf-8")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if error:
        distill_block = (
            f"distill_status: error\n"
            f"distill_date: \"{now}\"\n"
            f"distill_error: {json.dumps(error)}\n"
        )
    else:
        distill_block = (
            f"distill_status: {decision['decision']}\n"
            f"distill_date: \"{now}\"\n"
            f"distill_score:\n"
            f"  relevance: {decision['relevance']}\n"
            f"  quality: {decision['quality']}\n"
        )

    if content.startswith("---"):
        # Insert before closing ---
        parts = content.split("---", 2)
        if len(parts) >= 3:
            parts[1] = parts[1].rstrip() + "\n" + distill_block
            content = "---".join(parts)
    else:
        # No frontmatter — prepend
        content = f"---\n{distill_block}---\n\n{content}"

    filepath.write_text(content, encoding="utf-8")


# Provenance and dedup keys carried over from capture/ into raw/. These keys
# are the source of truth for `find_gdrive_file` and equivalent dedup scans,
# so they MUST survive normalization. Add new dedup keys here, not inline.
PROVENANCE_KEYS: tuple[str, ...] = (
    "source_url",
    "source_type",
    "gdrive_file_id",
    "gdrive_folder",
    "recording_id",
    "share_url",
    "session_id",
    "project",
    "meeting_date",
    "owner",
    "modified_at",
    "word_count",
    "attendees",
)


def _parse_frontmatter(content: str) -> tuple[dict, str]:
    """Split a markdown doc into (frontmatter dict, body). Empty dict if none."""
    if not content.startswith("---"):
        return {}, content
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content
    try:
        fm = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        fm = {}
    if not isinstance(fm, dict):
        fm = {}
    return fm, parts[2].lstrip("\n")


def promote_to_raw(capture_path: Path, decision: dict, content: str | None = None) -> Path:
    """Copy document to raw/ with normalized frontmatter.

    Provenance / dedup fields (gdrive_file_id, recording_id, etc.) are carried
    over from the capture file so that `find_gdrive_file`-style scans over
    raw/ continue to work after the file has left capture/.

    If `content` is provided, it is used directly instead of re-reading
    `capture_path`. This avoids a race with concurrent writers (e.g. the
    ClientBrain sync) that may delete or replace the capture file between
    the initial read and this call.
    """
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    if content is None:
        content = capture_path.read_text(encoding="utf-8")
    source_fm, body = _parse_frontmatter(content)
    decision_fm = decision.get("frontmatter") or {}

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    raw_frontmatter: dict = {
        "title": source_fm.get("title") or decision_fm.get("title") or capture_path.stem,
        "source_type": source_fm.get("source_type") or decision_fm.get("source_type") or "note",
        "source_url": source_fm.get("source_url") or decision_fm.get("source_url", ""),
        "date_ingested": now,
        "compiled_at": "",
        "tags": source_fm.get("tags") or decision_fm.get("tags") or [],
        "summary": source_fm.get("summary") or decision_fm.get("summary", ""),
    }

    # Carry over remaining provenance / dedup keys verbatim from the capture
    # frontmatter. Only include keys that actually have values so the raw
    # frontmatter stays readable.
    for key in PROVENANCE_KEYS:
        if key in raw_frontmatter:
            continue
        value = source_fm.get(key)
        if value not in (None, "", [], {}):
            raw_frontmatter[key] = value

    raw_content = (
        "---\n"
        + yaml.dump(raw_frontmatter, default_flow_style=False, sort_keys=False).strip()
        + "\n---\n\n"
        + body.strip()
        + "\n"
    )

    # Use same filename
    raw_path = RAW_DIR / capture_path.name
    raw_path.write_text(raw_content, encoding="utf-8")

    return raw_path


def main():
    parser = argparse.ArgumentParser(description="Meridian Daily Distill")
    parser.add_argument("--file", help="Process a specific capture file")
    parser.add_argument("--approve", help="Approve a pending promotion")
    parser.add_argument(
        "--promote-all",
        action="store_true",
        help="Promote every file in capture/ to raw/ without scoring (recovery mode)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Score without writing")
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max files to process this run (0 = no cap). Caps wall-clock to "
             "stay under the 600s receiver-job timeout.",
    )
    parser.add_argument(
        "--unquarantine",
        action="store_true",
        help="Move every file in .failed/ back into capture/ for retry, then exit.",
    )
    args = parser.parse_args()

    if args.unquarantine:
        moved = unquarantine_all()
        print(json.dumps({"status": "ok", "unquarantined": moved}))
        return

    config = load_config()

    # Restore any files stranded by a previously crashed run before we
    # enumerate work. This is cheap (typically a no-op) and makes the agent
    # self-healing across restarts.
    restored = recover_stranded()
    if restored:
        print(f"recover_stranded: restored {restored} file(s)", file=sys.stderr)

    failures = load_failures()
    results: list[dict] = []

    if args.promote_all:
        # Recovery path: bypass scoring and push everything in capture/ to raw/.
        # Use this when capture is wedged with legacy metadata or when scoring
        # has been unavailable (e.g. LLM outage).
        for path in sorted(CAPTURE_DIR.rglob("*.md")):
            try:
                raw_path = promote_to_raw(path, {"frontmatter": None})
                path.unlink()
                results.append({
                    "file": str(path),
                    "action": "force_promoted",
                    "raw_path": str(raw_path),
                })
            except Exception as e:
                print(f"Failed to promote {path.name}: {e}", file=sys.stderr)
                results.append({"file": str(path), "error": str(e)})
        print(json.dumps({"status": "ok", "processed": len(results), "results": results}, indent=2))
        return

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env

    if args.approve:
        # Direct approval — promote without re-scoring
        path = Path(args.approve)
        if not path.exists():
            print(f"Error: {path} not found", file=sys.stderr)
            sys.exit(1)
        raw_path = promote_to_raw(path, {"frontmatter": None})
        results.append({"file": str(path), "action": "approved", "raw_path": str(raw_path)})
        path.unlink()

    else:
        # Score and decide
        if args.file:
            files = [Path(args.file)]
        else:
            files = get_unprocessed_files()

        if not files:
            print("No unprocessed files in capture/", file=sys.stderr)
            print(json.dumps({"status": "ok", "processed": 0, "results": []}))
            return

        # Apply per-run cap so we stay under the 600s receiver-job timeout.
        # 0 (the default for CLI) disables the cap; the receiver passes 100.
        if args.limit and args.limit > 0:
            files = files[: args.limit]

        # Per-run staging dir. Files are atomically claimed into here before
        # scoring so concurrent writers (ClientBrain sync) can't yank them
        # out from under us.
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
        run_dir = PROCESSING_DIR / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        for original_path in files:
            # Stable failure key: capture-relative path. Survives renames
            # into staging, so failures attributed to the same logical file
            # accumulate correctly across runs.
            try:
                rel_key = str(original_path.relative_to(CAPTURE_DIR))
            except ValueError:
                rel_key = original_path.name

            staged: Path | None = None

            # Per-file isolation: any unexpected exception here logs,
            # records the failure, and continues so one bad file can't
            # wedge the whole batch.
            try:
                # --file mode passes a path that may already be absolute and
                # outside CAPTURE_DIR; only stage real capture/ files.
                if args.file:
                    staged = original_path
                else:
                    staged = claim_file(original_path, run_dir)
                    if staged is None:
                        print(f"Skipping {original_path.name}: lost claim race", file=sys.stderr)
                        continue

                print(f"Reviewing {staged.name}...", file=sys.stderr)
                try:
                    content = staged.read_text(encoding="utf-8", errors="replace")
                except FileNotFoundError:
                    print(f"Skipping {staged.name}: file vanished after claim", file=sys.stderr)
                    continue

                # Score the document. Failures are logged as metadata but
                # never block promotion — Sieve (upstream) is the human
                # review gate, so everything that reaches Meridian's
                # capture/ should flow through.
                decision: dict | None = None
                score_error: str | None = None
                try:
                    decision = score_document(client, content, config)
                except Exception as e:
                    score_error = str(e)
                    print(f"Error scoring {staged.name}: {e}", file=sys.stderr)

                if decision is None:
                    decision = {
                        "decision": "promote",
                        "relevance": 0,
                        "quality": 0,
                        "reasoning": f"scoring_error: {score_error}",
                        "error": score_error,
                    }

                result = {
                    "file": str(original_path),
                    "decision": decision.get("decision", "promote"),
                    "relevance": decision.get("relevance", 0),
                    "quality": decision.get("quality", 0),
                    "reasoning": decision.get("reasoning", ""),
                }
                if score_error:
                    result["error"] = score_error

                if not args.dry_run:
                    raw_path = promote_to_raw(staged, decision, content=content)
                    result["action"] = "promoted" if not score_error else "promoted_with_error"
                    result["raw_path"] = str(raw_path)
                    staged.unlink(missing_ok=True)

                # Success path — drop any prior failure record for this file.
                clear_failure(failures, rel_key)
                results.append(result)
            except Exception as e:
                err = str(e)
                count = record_failure(failures, rel_key, err)
                print(f"Failed on {original_path.name} (attempt {count}/{MAX_FAILURES}): {err}",
                      file=sys.stderr)
                if count >= MAX_FAILURES and staged is not None and staged.exists():
                    target = quarantine_file(staged, rel_key, failures.get(rel_key, {}))
                    if target is not None:
                        clear_failure(failures, rel_key)
                        results.append({
                            "file": str(original_path),
                            "error": err,
                            "action": "quarantined",
                            "quarantine_path": str(target),
                        })
                        continue
                # Otherwise: leave staged file in .processing/ so
                # recover_stranded() restores it for the next run.
                results.append({"file": str(original_path), "error": err})
                continue

        # Best-effort cleanup of the now-empty run dir.
        try:
            if run_dir.exists() and not any(run_dir.rglob("*")):
                shutil.rmtree(run_dir)
        except Exception:
            pass

        save_failures(failures)

    output = {
        "status": "ok",
        "processed": len(results),
        "results": results,
    }
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
