"""Tests for the Beacon Manifest class (v1).

Covers the manifest_implementation_task.md acceptance criteria:
  1. pickle round-trip via ``save``/``load``
  2. ``from_directory`` on the fixture repository
  3. edit -> new history entry, delete -> deletion token, recreate
  4. history merge rules (behind / ahead / identical / diverged)
  5. diverged + local fault -> no conflict
  6. diverged + remote fault -> no conflict
  7. A/D delete skipped when the local side has an issue
  8. ``clean_remote_issues`` behaviour

plus the CLI wrapper helpers from ``conftest``.
"""

import os
import time
from datetime import datetime, timezone
from pathlib import Path

from manifest import (
    Manifest,
    current_hash,
    is_deleted,
    is_subset,
    merge_histories,
)
from conftest import (
    actions_for,
    make_manifest,
    make_metadata,
    make_deleted_metadata,
    run_compare,
    run_create,
    run_show,
)

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def md(history):
    """Build a per-file metadata dict with the given token list."""
    return {"history": history, "timestamp": NOW}


# ------------------------------------------------------------------ pickle


def test_pickle_round_trip(tmp_path):
    m = make_manifest("repo", {"a.txt": make_metadata("h1")})
    m.add_issue("fault", "a.txt", description="boom")
    out = tmp_path / "m.pickle"
    m.save(out)

    loaded = Manifest.load(out)
    assert loaded.name == m.name
    assert loaded.path == m.path
    assert loaded.unit_id == m.unit_id
    assert loaded.files == m.files
    assert loaded.issues == m.issues


# ------------------------------------------------------------------ from_directory


def test_from_directory_scans_repository(repo_files):
    (repo_files / "uni_тест.txt").write_bytes("юникод".encode("utf-8"))
    (repo_files / "link.txt").symlink_to(repo_files / "a.txt")

    m = Manifest.from_directory("repo", repo_files, unit_id="u1")

    assert m.unit_id == "u1"
    assert set(m.files) == {
        "a.txt",
        "sub/b.txt",
        "data/empty.bin",
        "uni_тест.txt",
    }
    assert m.issues == []
    status = m.status()
    assert status["total_files"] == 4
    assert status["active_files"] == 4
    # empty file has the well-known md5 of b""
    assert current_hash(m.files["data/empty.bin"]) == (
        "d41d8cd98f00b204e9800998ecf8427e"
    )


# ------------------------------------------------------------------ scanning


def test_edit_appends_history_entry(repo_manifest, repo_files):
    m = repo_manifest
    before = list(m.files["a.txt"]["history"])
    target = repo_files / "a.txt"
    target.write_text("alpha2", encoding="utf-8")
    os.utime(target, (time.time() + 10, time.time() + 10))
    m.update_from_directory()

    after = m.files["a.txt"]["history"]
    assert len(after) == len(before) + 1
    assert after[: len(before)] == before
    assert m.issues == []


def test_delete_records_deletion_token(repo_manifest, repo_files):
    m = repo_manifest
    (repo_files / "sub" / "b.txt").unlink()
    m.update_from_directory()

    meta = m.files["sub/b.txt"]
    assert is_deleted(meta)
    assert meta["history"][-1] == "deleted:None"


def test_recreate_adds_new_entry(repo_manifest, repo_files):
    m = repo_manifest
    target = repo_files / "a.txt"
    target.unlink()
    m.update_from_directory()
    assert is_deleted(m.files["a.txt"])

    target.write_text("alpha-again", encoding="utf-8")
    os.utime(target, (time.time() + 10, time.time() + 10))
    m.update_from_directory()

    history = m.files["a.txt"]["history"]
    assert not is_deleted(m.files["a.txt"])
    assert len(history) == 3
    assert current_hash(m.files["a.txt"]) != current_hash(
        md([history[0]])
    )


def test_hash_change_without_mtime_change_is_fault(repo_manifest, repo_files):
    m = repo_manifest
    target = repo_files / "a.txt"
    st = target.stat()
    target.write_text("sneaky", encoding="utf-8")
    os.utime(target, (st.st_atime, st.st_mtime))
    m.update_from_directory()

    meta = m.files["a.txt"]
    assert not is_deleted(meta)
    assert len(meta["history"]) == 1
    assert m.has_issue("a.txt", types="fault")
    assert m.is_faulty()


# ------------------------------------------------------------------ helpers


def test_is_subset():
    assert is_subset(["h1"], ["h1", "h2"])
    assert is_subset([], ["h1"])
    assert is_subset(["a", "a"], ["a", "b", "a"])
    assert not is_subset(["a", "a"], ["a", "b"])
    assert not is_subset(["h1", "h2"], ["h1"])
    assert not is_subset(["h2", "h1"], ["h1", "h2"])
    assert not is_subset(["h1"], [])


def test_merge_histories():
    assert merge_histories(["a"], ["a"]) == ["a"]
    assert merge_histories(["a", "b"], ["a", "b", "c"]) == ["a", "b", "c"]
    assert merge_histories(["a", "c"], ["a", "b", "c"]) == ["a", "b", "c"]
    assert merge_histories(["a", "b", "d"], ["a", "c"]) == ["a", "b", "c", "d"]
    assert merge_histories(["a", "c"], ["a", "b", "d"]) == ["a", "b", "c", "d"]
    assert merge_histories([], ["a"]) == ["a"]


# ------------------------------------------------------------------ merge


def test_merge_identical_no_actions():
    m = make_manifest("repo", {"a": md(["hash:h1"])})
    peer = make_manifest("repo", {"a": md(["hash:h1"])})

    assert m.merge(peer, remote_id="u2") == []
    assert m.files["a"]["history"] == ["hash:h1"]
    assert m.issues == []


def test_merge_same_current_hash_merges_histories():
    m = make_manifest("repo", {"a": md(["hash:h1", "hash:h3"])})
    peer = make_manifest("repo", {"a": md(["hash:h1", "hash:h2", "hash:h3"])})

    assert m.merge(peer) == []
    assert m.files["a"]["history"] == ["hash:h1", "hash:h2", "hash:h3"]


def test_merge_adopts_file_missing_locally():
    m = make_manifest("repo", {"x": md(["hash:hx"])})
    peer = make_manifest("repo", {"a": md(["hash:ha"]), "x": md(["hash:hx"])})

    actions = m.merge(peer, remote_id="u2")
    assert actions_for(actions, "a") == [{"action": "fetch", "path": "a"}]
    assert m.files["a"]["history"] == ["hash:ha"]
    # the peer manifest must not be mutated
    assert peer.files is not m.files


def test_merge_adopts_deleted_only_history():
    m = make_manifest("repo", {})
    peer = make_manifest("repo", {"gone": make_deleted_metadata("u2")})

    assert m.merge(peer, remote_id="u2") == []
    assert m.files["gone"]["history"] == ["deleted:u2"]
    assert is_deleted(m.files["gone"])


def test_merge_local_only_file_untouched():
    m = make_manifest("repo", {"only": md(["hash:ho"])})
    peer = make_manifest("repo", {})

    assert m.merge(peer) == []
    assert m.files["only"]["history"] == ["hash:ho"]


def test_merge_local_deleted_remote_absent():
    m = make_manifest("repo", {"a": md(["deleted:u1"])})
    peer = make_manifest("repo", {})

    assert m.merge(peer) == []
    assert m.files["a"]["history"] == ["deleted:u1"]


def test_merge_local_behind_fetches():
    m = make_manifest("repo", {"a": md(["hash:h1"])})
    peer = make_manifest("repo", {"a": md(["hash:h1", "hash:h2"])})

    actions = m.merge(peer, remote_id="u2")
    assert actions_for(actions, "a") == [{"action": "fetch", "path": "a"}]
    assert m.files["a"]["history"] == ["hash:h1", "hash:h2"]
    assert m.issues == []


def test_merge_remote_behind_no_action():
    m = make_manifest("repo", {"a": md(["hash:h1", "hash:h2"])})
    peer = make_manifest("repo", {"a": md(["hash:h1"])})

    assert m.merge(peer) == []
    assert m.files["a"]["history"] == ["hash:h1", "hash:h2"]
    assert m.issues == []


def test_merge_diverged_registers_conflict():
    m = make_manifest("repo", {"a": md(["hash:h1"])})
    peer = make_manifest("repo", {"a": md(["hash:h2"])})

    assert m.merge(peer, remote_id="u2") == []
    assert m.has_issue("a", types="conflict")
    assert m.issues[0]["remote"] == "u2"
    # local file is kept untouched
    assert m.files["a"]["history"] == ["hash:h1"]
    assert not is_deleted(m.files["a"])



def test_merge_diverged_with_local_fault_no_conflict():
    m = make_manifest("repo", {"a": md(["hash:h1"])})
    m.add_issue("fault", "a", description="local fault")
    peer = make_manifest("repo", {"a": md(["hash:h2"])})

    assert m.merge(peer, remote_id="u2") == []
    assert not m.has_issue("a", types="conflict")
    assert m.is_faulty()


def test_merge_diverged_with_remote_fault_no_conflict():
    m = make_manifest("repo", {"a": md(["hash:h1"])})
    peer = make_manifest("repo", {"a": md(["hash:h2"])})
    peer.add_issue("fault", "a", description="remote fault")

    assert m.merge(peer, remote_id="u2") == []
    assert not m.has_issue("a", types="conflict")


def test_merge_delete_when_consistent():
    m = make_manifest("repo", {"a": md(["hash:h1", "hash:h2"])})
    peer = make_manifest("repo", {"a": md(["hash:h1", "hash:h2", "deleted:u2"])})

    actions = m.merge(peer, remote_id="u2")
    assert actions_for(actions, "a") == [{"action": "delete", "path": "a"}]
    assert is_deleted(m.files["a"])
    assert m.files["a"]["history"] == ["hash:h1", "hash:h2", "deleted:u2"]


def test_merge_delete_skipped_when_local_has_issue():
    m = make_manifest("repo", {"a": md(["hash:h1"])})
    m.add_issue("conflict", "a", remote="u9")
    peer = make_manifest("repo", {"a": md(["hash:h1", "deleted:u2"])})

    assert m.merge(peer, remote_id="u2") == []
    assert not is_deleted(m.files["a"])


def test_merge_delete_remote_behind_no_action():
    m = make_manifest("repo", {"a": md(["hash:h1", "deleted:u2", "hash:h2"])})
    peer = make_manifest("repo", {"a": md(["hash:h1", "deleted:u2"])})

    assert m.merge(peer, remote_id="u2") == []
    assert m.files["a"]["history"] == ["hash:h1", "deleted:u2", "hash:h2"]


def test_merge_delete_diverged_registers_conflict():
    m = make_manifest("repo", {"a": md(["hash:h1"])})
    peer = make_manifest("repo", {"a": md(["hash:h2", "deleted:u2"])})

    assert m.merge(peer, remote_id="u2") == []
    assert m.has_issue("a", types="conflict")
    assert not is_deleted(m.files["a"])


def test_merge_local_deleted_remote_ahead_fetches():
    m = make_manifest("repo", {"a": md(["hash:h1", "deleted:u1"])})
    peer = make_manifest("repo", {"a": md(["hash:h1", "deleted:u1", "hash:h2"])})

    actions = m.merge(peer, remote_id="u2")
    assert actions_for(actions, "a") == [{"action": "fetch", "path": "a"}]
    assert not is_deleted(m.files["a"])
    assert m.files["a"]["history"] == ["hash:h1", "deleted:u1", "hash:h2"]


def test_merge_local_deleted_remote_behind_no_action():
    m = make_manifest("repo", {"a": md(["hash:h1", "deleted:u1", "hash:h2"])})
    peer = make_manifest("repo", {"a": md(["hash:h1", "deleted:u1"])})

    assert m.merge(peer, remote_id="u2") == []
    assert not is_deleted(m.files["a"])


def test_merge_local_deleted_remote_diverged_conflict():
    m = make_manifest("repo", {"a": md(["hash:h1", "deleted:u1"])})
    peer = make_manifest("repo", {"a": md(["hash:h9"])})

    assert m.merge(peer, remote_id="u2") == []
    assert m.has_issue("a", types="conflict")


def test_merge_both_deleted_merges_histories():
    m = make_manifest("repo", {"a": md(["hash:h1", "deleted:u1"])})
    peer = make_manifest("repo", {"a": md(["hash:h1", "deleted:u2"])})

    assert m.merge(peer, remote_id="u2") == []
    assert m.files["a"]["history"] == ["hash:h1", "deleted:u1", "deleted:u2"]


def test_merge_empty_local_adopts_whole_repo():
    m = make_manifest("repo", {})
    peer = make_manifest(
        "repo",
        {
            "a": md(["hash:ha"]),
            "s/b": md(["hash:hb"]),
            "gone": make_deleted_metadata("u2"),
        },
    )

    actions = m.merge(peer, remote_id="u2")
    assert sorted(a["path"] for a in actions) == ["a", "s/b"]
    assert m.files["a"]["history"] == ["hash:ha"]
    assert m.files["gone"]["history"] == ["deleted:u2"]


# ------------------------------------------------------------------ issues


def test_clean_remote_issues_removes_only_matching():
    m = make_manifest("repo", {"a": md(["hash:ha"]), "b": md(["hash:hb"])})
    m.add_issue("conflict", "a", remote="u2")
    m.add_issue("conflict", "b", remote="u3")
    m.add_issue("fault", "a", remote="u2")

    removed = m.clean_remote_issues("u2")
    assert [i["path"] for i in removed] == [Path("a")]
    assert m.has_issue("a", types="fault")
    assert m.has_issue("b", types="conflict")
    assert not m.has_issue("a", types="conflict")

    # a second call removes nothing
    assert m.clean_remote_issues("u2") == []
    assert len(m.issues) == 2


def test_has_issue_and_resolve_issue():
    m = make_manifest("repo", {"a": make_metadata("h1")})
    assert not m.has_issue("a")

    m.add_issue("conflict", "a", remote="u2")
    assert m.has_issue("a")
    assert m.has_issue("a", types="conflict")
    assert not m.has_issue("a", types="fault")
    assert not m.is_faulty()

    m.add_issue("fault", "a")
    assert m.is_faulty()

    m.resolve_issue("a", "conflict")
    assert not m.has_issue("a", types="conflict")
    assert m.has_issue("a", types="fault")

    m.resolve_issue("a", "fault")
    assert not m.is_faulty()
    assert m.issues == []


# ------------------------------------------------------------------ CLI


def test_create_and_compare_identical(repo_files, tmp_path):
    a = tmp_path / "a.pickle"
    b = tmp_path / "b.pickle"
    assert run_create("repo", repo_files, a) == 0
    assert run_create("repo", repo_files, b) == 0
    assert run_compare(a, b) == 0


def test_compare_different(repo_files, tmp_path):
    a = tmp_path / "a.pickle"
    b = tmp_path / "b.pickle"
    assert run_create("repo", repo_files, a) == 0
    (repo_files / "a.txt").write_text("changed", encoding="utf-8")
    assert run_create("repo", repo_files, b) == 0
    assert run_compare(a, b) == 1


def test_show_manifest(repo_files, tmp_path):
    out = tmp_path / "m.pickle"
    assert run_create("repo", repo_files, out) == 0
    assert run_show(out) == 0
    assert run_show(out, all_files=True) == 0
