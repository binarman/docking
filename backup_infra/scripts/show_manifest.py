#!/usr/bin/env python3
"""Show a Beacon manifest (v1).

Usage:
    show_manifest.py --file MANIFEST [--all]
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from manifest import current_hash, is_deleted, Manifest  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="show a Beacon manifest")
    parser.add_argument("--file", required=True, help="manifest file to show")
    parser.add_argument(
        "--all",
        action="store_true",
        help="list all files with their state",
    )
    args = parser.parse_args()

    try:
        manifest = Manifest.load(args.file)
    except (OSError, ValueError, TypeError) as error:
        print(f"error: cannot load manifest {args.file}: {error}", file=sys.stderr)
        return 2

    status = manifest.status()
    print(f"Repository: {status['name']} (v{status['version']})")
    print(f"Path: {status['path']}")
    print(
        f"Files: {status['total_files']} total, "
        f"{status['active_files']} active, "
        f"{status['deleted_files']} deleted"
    )
    if status["faulty"]:
        print("Status: FAULTY")
    else:
        print("Status: OK")

    if status["issues"]:
        print(f"\nIssues ({len(status['issues'])}):")
        for issue in status["issues"]:
            remote = f" (remote: {issue['remote']})" if issue["remote"] else ""
            print(f"  {issue['type']}: {issue['path']}{remote}")
            if issue["description"]:
                print(f"    {issue['description']}")
    else:
        print("\nNo issues")

    if args.all:
        print("\nFiles:")
        for rel_path in sorted(manifest.files):
            metadata = manifest.files[rel_path]
            history = metadata.get("history") or []
            if is_deleted(metadata):
                state = f"DELETED ({history[-1]})"
            else:
                state = current_hash(metadata)
            print(
                f"  {rel_path}\n"
                f"    state: {state}\n"
                f"    modified: {metadata.get('timestamp')}\n"
                f"    history: {len(history)} entries"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
