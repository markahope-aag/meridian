"""One-off: archive uncompiled raw/ docs of bulk-import source types.

Moves uncompiled internal-{email,slack,clickup} from raw/ to raw/.archive/.
Already-compiled files are left in place. Reversible: just `mv` the file
back to /meridian/raw/.

Usage:
    python3 archive-bulk-import.py            # dry-run, prints what would move
    python3 archive-bulk-import.py --execute  # actually move
"""
import argparse
import re
import shutil
import sys
from pathlib import Path

RAW = Path("/meridian/raw")
ARCHIVE = RAW / ".archive"

ARCHIVE_TYPES = {"internal-email", "internal-slack", "internal-clickup"}


def is_uncompiled(head: str) -> bool:
    m = re.search(r"compiled_at:\s*['\"]?([^'\"\n]*)", head)
    if not m:
        return True
    return m.group(1).strip() in ("", "null", "~")


def get_source_type(head: str) -> str:
    m = re.search(r"source_type:\s*([^\n]+)", head)
    return m.group(1).strip() if m else ""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true",
                        help="Actually move files. Default is dry-run.")
    args = parser.parse_args()

    if args.execute:
        ARCHIVE.mkdir(parents=True, exist_ok=True)

    moved = 0
    kept = 0
    compiled_skipped = 0
    type_moved = {t: 0 for t in ARCHIVE_TYPES}

    for f in RAW.glob("*.md"):
        if f.name.startswith("_"):
            continue
        head = f.read_text(encoding="utf-8", errors="replace")[:800]

        if not is_uncompiled(head):
            compiled_skipped += 1
            kept += 1
            continue

        stype = get_source_type(head)
        if stype not in ARCHIVE_TYPES:
            kept += 1
            continue

        if args.execute:
            target = ARCHIVE / f.name
            shutil.move(str(f), str(target))
        type_moved[stype] += 1
        moved += 1

    mode = "MOVED" if args.execute else "WOULD MOVE"
    print(f"{mode}: {moved}")
    for t, c in sorted(type_moved.items()):
        print(f"  {t}: {c}")
    print(f"KEPT IN raw/: {kept} (incl. {compiled_skipped} already-compiled)")


if __name__ == "__main__":
    main()
