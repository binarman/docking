#!/usr/bin/env python3
"""Compare two Beacon manifests (v1).

Exit codes: 0 if the manifests describe identical repositories,
1 if they differ, 2 on usage/load errors.

Usage:
    compare_manifests.py --a MANIFEST1 --b MANIFEST2
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from manifest import Manifest  # noqa: E402


def _load(label, path):
    try:
        return Manifest.load(path)
    except (OSError, ValueError, TypeError) as error:
        print(
            f"error: cannot load {label} manifest {path}: {error}", file=sys.stderr
        )
        return None


def compare(a_path, b_path):
    manifest_a = _load("A", a_path)
    if manifest_a is None:
        return 2
    manifest_b = _load("B", b_path)
    if manifest_b is None:
        return 2

    diff = manifest_a.diff(manifest_b)
    print(f"Comparing: {manifest_a.name} ({a_path})")
    print(f"    with : {manifest_b.name} ({b_path})")
    if not diff["same_name"]:
        print(
            f"WARNING: repository names differ "
            f"({manifest_a.name!r} vs {manifest_b.name!r})"
        )

    for rel_path in diff["only_local"]:
        print(f"only in A: {rel_path}")
    for rel_path in diff["only_remote"]:
        print(f"only in B: {rel_path}")
    for rel_path, hash_a, hash_b in diff["hash_mismatches"]:
        print(f"hash mismatch: {rel_path} (A: {hash_a}, B: {hash_b})")
    for rel_path, state_a, state_b in diff["status_mismatches"]:
        print(f"status mismatch: {rel_path} (A: {state_a}, B: {state_b})")

    for label, manifest in (("A", manifest_a), ("B", manifest_b)):
        if manifest.issues:
            print(f"\nIssues in {label} ({len(manifest.issues)}):")
            for issue in manifest.issues:
                remote = f" (remote: {issue['remote']})" if issue["remote"] else ""
                print(f"  {issue['type']}: {issue['path']}{remote}")
                if issue["description"]:
                    print(f"    {issue['description']}")

    if diff["identical"]:
        print("\nManifests are identical")
        return 0
    print("\nManifests differ")
    return 1


def main():
    parser = argparse.ArgumentParser(description="compare two Beacon manifests")
    parser.add_argument("--a", required=True, help="first manifest file")
    parser.add_argument("--b", required=True, help="second manifest file")
    args = parser.parse_args()
    return compare(args.a, args.b)


if __name__ == "__main__":
    sys.exit(main())