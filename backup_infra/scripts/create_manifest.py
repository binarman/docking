#!/usr/bin/env python3
"""Create a Beacon manifest (v1) by scanning a directory.

Usage:
    create_manifest.py --name REPO --dir ROOT [--out FILE] [--verbose]
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from manifest import MANIFEST_VERSION, Manifest  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="create a Beacon manifest")
    parser.add_argument("--name", required=True, help="repository name")
    parser.add_argument("--dir", required=True, help="repository root directory")
    parser.add_argument(
        "--out",
        default="manifest.pickle",
        help="output manifest file (default: manifest.pickle)",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="print per-file progress"
    )
    args = parser.parse_args()

    root = Path(args.dir)
    if not root.is_dir():
        print(f"error: not a directory: {root}", file=sys.stderr)
        return 2
    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = Path.cwd() / out_path

    total = 0
    if args.verbose:
        for _dirpath, _dirnames, filenames in os.walk(root):
            total += len(filenames)
        print(f"Scanning {root} ...")
        print(f"Found {total} files (symlinks are skipped)")

    manifest = Manifest.from_directory(args.name, root)

    if args.verbose:
        status = manifest.status()
        for issue in status["issues"]:
            print(f"  FAULT [{issue['path']}]: {issue['description']}")
        if manifest.issues:
            print(f"  {len(manifest.issues)} fault issue(s)")
        print(
            f"Tracked {status['total_files']} file(s) "
            f"({status['active_files']} active, "
            f"{status['deleted_files']} deleted)"
        )

    manifest.save(out_path)
    print(f"Manifest saved to {out_path}")
    print(
        f"Repository: {manifest.name} (v{MANIFEST_VERSION}) | "
        f"Path: {manifest.path} | Files: {len(manifest.files)} | "
        f"Issues: {len(manifest.issues)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())