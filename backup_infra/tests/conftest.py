"""Shared fixtures and helpers for the Beacon manifest tests."""

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

# make backup_infra/scripts importable regardless of the pytest rootdir
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from manifest import (  # noqa: E402
    MANIFEST_VERSION,
    Manifest,
    current_hash,
    is_deleted,
)


def make_metadata(content_hash):
    return {
        "history": [f"hash:{content_hash}"],
        "timestamp": datetime.now(timezone.utc),
    }


def make_deleted_metadata(token):
    return {
        "history": [f"deleted:{token}"],
        "timestamp": datetime.now(timezone.utc),
    }


def make_manifest(name="repo", files=None, issues=None, path="/data/repo"):
    """Build a manifest object directly (no directory scan)."""
    manifest = Manifest(name, path)
    manifest.files = files or {}
    manifest.issues = list(issues or [])
    return manifest


def actions_for(actions, rel_path):
    return [a for a in actions if a["path"] == rel_path]


def run_create(name, root, out_path, verbose=False):
    """Run scripts/create_manifest.py and return its exit code."""
    from create_manifest import main as create_main

    argv = sys.argv
    sys.argv = ["create_manifest.py", "--name", name, "--dir", str(root)]
    if verbose:
        sys.argv.append("--verbose")
    sys.argv.append("--out")
    sys.argv.append(str(out_path))
    try:
        return create_main()
    finally:
        sys.argv = argv


def run_show(manifest_path, all_files=False):
    """Run scripts/show_manifest.py and return its exit code."""
    from show_manifest import main as show_main

    argv = sys.argv
    sys.argv = ["show_manifest.py", "--file", str(manifest_path)]
    if all_files:
        sys.argv.append("--all")
    try:
        return show_main()
    finally:
        sys.argv = argv


def run_compare(path_a, path_b):
    """Run scripts/compare_manifests.py and return its exit code."""
    from compare_manifests import main as compare_main

    argv = sys.argv
    sys.argv = ["compare_manifests.py", "--a", str(path_a), "--b", str(path_b)]
    try:
        return compare_main()
    finally:
        sys.argv = argv


@pytest.fixture
def repo_files(tmp_path):
    """A small repository tree plus helpers to manipulate it.

    Layout:
        a.txt            -> "alpha"
        sub/b.txt        -> "beta"
        data/empty.bin   -> b""
    """
    root = tmp_path / "repo"
    (root / "sub").mkdir(parents=True)
    (root / "data").mkdir()
    (root / "a.txt").write_text("alpha", encoding="utf-8")
    (root / "sub" / "b.txt").write_text("beta", encoding="utf-8")
    (root / "data" / "empty.bin").write_bytes(b"")
    return root


@pytest.fixture
def repo_manifest(repo_files):
    return Manifest.from_directory("repo", repo_files)