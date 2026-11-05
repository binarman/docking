#!/usr/bin/env python3
"""Beacon manifest module (v1).

A manifest is a pickled ``Manifest`` object describing one local repository:

- ``version``     : format version (1).
- ``name``        : repository name (repos with the same name belong to one
  virtual repository).
- ``path``        : absolute local root directory of the repository.
- ``files``       : dict mapping repository-relative POSIX paths to metadata
  ``{"history": ["hash:<md5>" | "deleted:<unit name>", ...],
  "timestamp": datetime}``.
  The last history entry is the file's current state: a ``hash:`` entry means
  the file is present, a ``deleted:`` entry means it is recorded as deleted.
- ``issues``      : list of dicts ``{"type": ..., "path": PosixPath,
  "remote": str | None, "description": str}`` where ``type`` is one of
  ``fault`` (I/O error or integrity check failed) or
  ``conflict`` (synchronization).

``Manifest`` is the single source of truth for manifest content: the debug
tools (``create_manifest.py``, ``show_manifest.py``, ``compare_manifests.py``)
and the future ``unit.py`` synchronization all go through it.
"""

import hashlib
import os
import pickle
import stat
import tempfile
from datetime import datetime
from pathlib import Path

MANIFEST_VERSION = 1
ISSUE_TYPES = ("fault", "conflict")

HASH_PREFIX = "hash:"
DELETED_PREFIX = "deleted:"

_CHUNK_SIZE = 1024 * 1024


def hash_file(path):
    """Return the hex md5 digest of the file at ``path``."""
    digest = hashlib.md5()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def make_hash_entry(file_hash):
    return f"{HASH_PREFIX}{file_hash}"


def make_deleted_entry(unit_id):
    return f"{DELETED_PREFIX}{unit_id}"


def current_hash(metadata):
    """Current file hash from a metadata dict, or ``None`` if absent/deleted."""
    history = metadata.get("history") or []
    last = history[-1] if history else None
    if last is not None and last.startswith(HASH_PREFIX):
        return last[len(HASH_PREFIX):]
    return None


def is_deleted(metadata):
    """Whether a metadata dict records the file as currently deleted."""
    history = metadata.get("history") or []
    return bool(history) and history[-1].startswith(DELETED_PREFIX)


def is_subset(subset, full):
    """Whether ``subset`` is an order-preserving subsequence of ``full``."""
    full_pos = 0
    for token in subset:
        while full_pos < len(full) and full[full_pos] != token:
            full_pos += 1
        if full_pos == len(full):
            return False
        full_pos += 1
    return True


def merge_histories(h1, h2):
    """Merge two histories into one.

    The order of events in each individual history is preserved and the
    result is the same no matter in which order the histories are provided.
    """
    merged = []
    p1, p2 = 0, 0
    while p1 < len(h1) or p2 < len(h2):
        if p1 < len(h1) and p2 == len(h2):
            merged.append(h1[p1])
            p1 += 1
        elif p2 < len(h2) and p1 == len(h1):
            merged.append(h2[p2])
            p2 += 1
        elif h1[p1] == h2[p2]:
            merged.append(h1[p1])
            p1 += 1
            p2 += 1
        elif h1[p1] < h2[p2]:
            merged.append(h1[p1])
            p1 += 1
        else:
            merged.append(h2[p2])
            p2 += 1
    return merged


class Manifest:
    def __init__(self, name, path, unit_id=None):
        self.version = MANIFEST_VERSION
        self.name = str(name)
        self.path = Path(path)
        self.files = {}
        self.issues = []
        self.unit_id = unit_id

    # ------------------------------------------------------------- building

    @classmethod
    def from_directory(cls, name, root, unit_id=None):
        """Build a new manifest by scanning ``root`` recursively."""
        root = Path(root)
        if not root.is_dir():
            raise ValueError(f"not a directory: {root}")
        manifest = cls(name, root.resolve(), unit_id)
        manifest._scan(root)
        return manifest


    def update_from_directory(self, root=None):
        """Rescan the repository and update this manifest in place.

        New files get new entries, changed hashes append a history entry and
        refresh the timestamp, files missing from disk get a deletion token
        and recreated files get a new hash entry appended.
        `root` argument is intended for test purposes, not for normal use.
        """
        if root is None:
            root = self.path
        root = Path(root)
        if not root.is_dir():
            raise ValueError(f"not a directory: {root}")
        self.path = root.resolve()
        self._scan(root)


    def _scan(self, root):
        for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
            for filename in filenames:
                full_path = Path(dirpath) / filename
                rel_path = full_path.relative_to(root).as_posix()
                try:
                    st = os.lstat(full_path)
                except OSError as error:
                    self.add_issue(
                        "fault",
                        rel_path,
                        description=f"cannot stat file: {error}",
                    )
                    continue
                if not stat.S_ISREG(st.st_mode):
                    # symlinks, fifos, sockets, ... are never tracked
                    continue
                try:
                    file_hash = hash_file(full_path)
                except OSError as error:
                    self.add_issue(
                        "fault",
                        rel_path,
                        description=f"cannot read file: {error}",
                    )
                    continue
                metadata = self.files.get(rel_path)
                new_timestamp = datetime.fromtimestamp(st.st_mtime)
                if metadata is None:
                    metadata = {
                        "history": [make_hash_entry(file_hash)],
                        "timestamp": new_timestamp,
                    }
                    self.files[rel_path] = metadata
                elif current_hash(metadata) != file_hash:
                    if new_timestamp == metadata["timestamp"]:
                        self.add_issue(
                            "fault",
                            rel_path,
                            description=(
                                "file hash changed without a modification time "
                                f"change: {current_hash(metadata)} != {file_hash}"
                            ),
                        )
                        continue
                    metadata["history"].append(make_hash_entry(file_hash))
                    metadata["timestamp"] = new_timestamp
                else:
                    metadata["timestamp"] = new_timestamp
        self._record_deletions(root)

    def _record_deletions(self, root):
        """Append a deletion token for tracked files missing from ``root``."""
        for rel_path in sorted(self.files):
            if (root / rel_path).is_file():
                continue
            metadata = self.files[rel_path]
            if not is_deleted(metadata):
                metadata["history"].append(make_deleted_entry(self.unit_id))
                metadata["timestamp"] = datetime.now()

    # --------------------------------------------------------------- issues

    def add_issue(self, issue_type, path, remote=None, description=""):
        """Add (or refresh) an issue for ``path``.

        Issues are deduplicated per (type, path) so repeated scans and merges
        do not accumulate duplicates.
        """
        if issue_type not in ISSUE_TYPES:
            raise ValueError(
                f"unknown issue type: {issue_type!r} "
                f"(expected one of {ISSUE_TYPES})"
            )
        path = Path(path)
        for issue in self.issues:
            if issue["type"] == issue_type and issue["path"] == path:
                if remote is not None:
                    issue["remote"] = remote
                if description:
                    issue["description"] = description
                return
        self.issues.append(
            {
                "type": issue_type,
                "path": path,
                "remote": remote,
                "description": str(description),
            }
        )

    def is_faulty(self):
        """Whether the repository has any unresolved ``fault`` issue."""
        return any(issue["type"] == "fault" for issue in self.issues)

    def has_issue(self, path, types=None):
        """Whether an unresolved issue exists for ``path``.

        When ``types`` is given, only issue types in that collection count.
        """
        path = Path(path)
        return any(
            issue["path"] == path
            and (types is None or issue["type"] in types)
            for issue in self.issues
        )

    def resolve_issue(self, path, issue_type):
        """Remove the matching issue only; re-hashing is the caller's job."""
        path = Path(path)
        self.issues = [
            issue
            for issue in self.issues
            if not (issue["path"] == path and issue["type"] == issue_type)
        ]

    # ------------------------------------------------------------ reporting

    def status(self):
        """Plain-data summary suitable for display or logging."""
        active = 0
        deleted = 0
        for metadata in self.files.values():
            if is_deleted(metadata):
                deleted += 1
            else:
                active += 1
        return {
            "name": self.name,
            "path": str(self.path),
            "version": self.version,
            "total_files": len(self.files),
            "active_files": active,
            "deleted_files": deleted,
            "faulty": self.is_faulty(),
            "issues": [
                {
                    "type": issue["type"],
                    "path": str(issue["path"]),
                    "remote": issue["remote"],
                    "description": issue["description"],
                }
                for issue in self.issues
            ],
        }

    def diff(self, other):
        """Compare file states with another manifest (ignoring issues).

        Returns a dict with ``same_name``, ``only_local``, ``only_remote``,
        ``hash_mismatches``, ``status_mismatches`` and ``identical``.
        """
        only_local = sorted(set(self.files) - set(other.files))
        only_remote = sorted(set(other.files) - set(self.files))
        hash_mismatches = []
        status_mismatches = []
        for rel_path in sorted(set(self.files) & set(other.files)):
            local_meta = self.files[rel_path]
            remote_meta = other.files[rel_path]
            local_deleted = is_deleted(local_meta)
            remote_deleted = is_deleted(remote_meta)
            if local_deleted != remote_deleted:
                status_mismatches.append(
                    (
                        rel_path,
                        "deleted" if local_deleted else "current",
                        "deleted" if remote_deleted else "current",
                    )
                )
            elif not local_deleted and current_hash(local_meta) != current_hash(
                remote_meta
            ):
                hash_mismatches.append(
                    (
                        rel_path,
                        current_hash(local_meta),
                        current_hash(remote_meta),
                    )
                )
        same_name = self.name == other.name
        identical = (
            same_name
            and not only_local
            and not only_remote
            and not hash_mismatches
            and not status_mismatches
        )
        return {
            "same_name": same_name,
            "only_local": only_local,
            "only_remote": only_remote,
            "hash_mismatches": hash_mismatches,
            "status_mismatches": status_mismatches,
            "identical": identical,
        }

    # -------------------------------------------------------------- merging

    def clean_remote_issues(self, remote_id):
        """Remove conflict issues recorded against ``remote_id``.

        Before a new synchronization round the caller removes the conflict
        issues that were registered for the remote being synced, so they can
        be re-evaluated from the fresh remote state.  Issues pointing at any
        other remote (or any non-conflict type) are left intact.

        Returns the list of removed issue dicts.
        """
        removed = []
        kept = []
        for issue in self.issues:
            if issue["type"] == "conflict" and issue["remote"] == remote_id:
                removed.append(issue)
            else:
                kept.append(issue)
        self.issues = kept
        return removed

    def merge(self, other, remote_id=None):
        """Merge the peer manifest ``other`` into this (local) manifest.

        ``remote_id`` is the ``unit identifier`` of the peer unit, recorded
        on conflict issues. Returns a list of action dicts. One per path
        which requires update from the remote, i.e. download a new version of
        the file or delete the file. Never mutates ``other``.
        """
        actions = []
        for rel_path in sorted(set(self.files) | set(other.files)):
            local_meta = self.files.get(rel_path)
            remote_meta = other.files.get(rel_path)
            if local_meta is None:
                if remote_meta is None:
                    continue
                # N + A/B or N + D: adopt the remote history.
                self.files[rel_path] = self._copy_metadata(remote_meta)
                if not is_deleted(remote_meta):
                    actions.append({"action": "fetch", "path": rel_path})
                continue
            if remote_meta is None:
                continue  # A/D + N: do nothing
            local_deleted = is_deleted(local_meta)
            remote_deleted = is_deleted(remote_meta)

            if local_deleted and remote_deleted:
                # D + D: merge the remote history into the local one.
                local_meta["history"] = merge_histories(
                    local_meta["history"], remote_meta["history"]
                )
                local_meta["timestamp"] = remote_meta["timestamp"]
                continue

            if not local_deleted and not remote_deleted:
                local_hash = current_hash(local_meta)
                remote_hash = current_hash(remote_meta)
                if local_hash == remote_hash:
                    # A + A: merge the remote history into the local one.
                    local_meta["history"] = merge_histories(
                        local_meta["history"], remote_meta["history"]
                    )
                    local_meta["timestamp"] = remote_meta["timestamp"]
                elif is_subset(local_meta["history"], remote_meta["history"]):
                    # A/B, local is behind: fetch and merge.
                    local_meta["history"] = merge_histories(
                        local_meta["history"], remote_meta["history"]
                    )
                    local_meta["timestamp"] = remote_meta["timestamp"]
                    actions.append({"action": "fetch", "path": rel_path})
                elif not is_subset(
                    remote_meta["history"], local_meta["history"]
                ):
                    self._register_conflict(
                        other, rel_path, remote_id, local_meta, remote_meta
                    )
                continue

            if not local_deleted:
                # A + D: delete only if the local history is consistent
                # with the remote one and the local side has no issues.
                if is_subset(local_meta["history"], remote_meta["history"]):
                    if self.has_issue(rel_path):
                        continue
                    local_meta["history"] = merge_histories(
                        local_meta["history"], remote_meta["history"]
                    )
                    local_meta["timestamp"] = remote_meta["timestamp"]
                    actions.append({"action": "delete", "path": rel_path})
                elif not is_subset(remote_meta["history"], local_meta["history"]):
                    self._register_conflict(
                        other, rel_path, remote_id, local_meta, remote_meta
                    )
                continue

            # D + A/B: the file was deleted locally, the remote still has it.
            if is_subset(local_meta["history"], remote_meta["history"]):
                local_meta["history"] = merge_histories(
                    local_meta["history"], remote_meta["history"]
                )
                local_meta["timestamp"] = remote_meta["timestamp"]
                actions.append({"action": "fetch", "path": rel_path})
            elif not is_subset(remote_meta["history"], local_meta["history"]):
                self._register_conflict(
                    other, rel_path, remote_id, local_meta, remote_meta
                )
        return actions

    @staticmethod
    def _copy_metadata(metadata):
        """Deep-copy a per-file metadata dict."""
        return {
            "history": list(metadata["history"]),
            "timestamp": metadata["timestamp"],
        }

    def _register_conflict(self, other, rel_path, remote_id, local_meta, remote_meta):
        """Add a conflict issue unless either side is faulty at the path.

        Faulty repositories must wait for a human, so no conflict is
        registered when an unresolved fault already exists on the path.
        """
        if self.has_issue(rel_path, types="fault") or other.has_issue(
            rel_path, types="fault"
        ):
            return
        self.add_issue(
            "conflict",
            rel_path,
            remote=remote_id,
            description=(
                "diverged histories: local="
                f"{current_hash(local_meta) or 'deleted'}, "
                f"remote={current_hash(remote_meta) or 'deleted'}"
            ),
        )


    # ----------------------------------------------------------- persistence

    def save(self, path):
        """Atomically pickle this manifest to ``path`` (temp file + rename)."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        try:
            with os.fdopen(fd, "wb") as handle:
                pickle.dump(self, handle, protocol=pickle.HIGHEST_PROTOCOL)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    @classmethod
    def load(cls, path):
        """Load and validate a manifest from ``path``.

        Raises ``ValueError`` for foreign pickles or unsupported versions.
        """
        with open(path, "rb") as handle:
            data = pickle.load(handle)
        if not isinstance(data, Manifest):
            raise ValueError(
                f"not a manifest file "
                f"(unexpected object {type(data).__name__!r})"
            )
        if data.version != MANIFEST_VERSION:
            raise ValueError(
                f"unsupported manifest version {data.version!r} "
                f"(expected {MANIFEST_VERSION})"
            )
        return data
