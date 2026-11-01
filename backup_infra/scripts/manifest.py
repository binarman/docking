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
  ``fault`` (I/O error), ``unexpected_hash`` (integrity check) or
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
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

MANIFEST_VERSION = 1
ISSUE_TYPES = ("fault", "unexpected_hash", "conflict")

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


def make_deleted_entry():
    token = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return f"{DELETED_PREFIX}{token}"


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


def deletion_token(metadata):
    """The current ``deleted:`` token of a deleted entry (``None`` otherwise)."""
    history = metadata.get("history") or []
    last = history[-1] if history else None
    if last is not None and last.startswith(DELETED_PREFIX):
        return last
    return None


class Manifest:
    def __init__(self, name, path):
        self.version = MANIFEST_VERSION
        self.name = str(name)
        self.path = Path(path)
        self.files = {}
        self.issues = []

    # ------------------------------------------------------------- building

    @classmethod
    def from_directory(cls, name, root):
        """Build a new manifest by scanning ``root`` recursively."""
        root = Path(root)
        if not root.is_dir():
            raise ValueError(f"not a directory: {root}")
        manifest = cls(name, root.resolve())
        manifest._scan(root)
        return manifest

    def update_from_directory(self, root=None):
        """Rescan the repository and update this manifest in place.

        New files get new entries, changed hashes append a history entry and
        refresh the timestamp, files missing from disk get a deletion token
        and recreated files get a new hash entry appended.
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
                try:
                    st = os.lstat(full_path)
                except OSError as error:
                    self.add_issue(
                        "fault",
                        full_path,
                        description=f"cannot stat file: {error}",
                    )
                    continue
                if not stat.S_ISREG(st.st_mode):
                    # symlinks, fifos, sockets, ... are never tracked
                    continue
                rel_path = full_path.relative_to(root).as_posix()
                try:
                    file_hash = hash_file(full_path)
                except OSError as error:
                    self.add_issue(
                        "fault",
                        full_path,
                        description=f"cannot read file: {error}",
                    )
                    continue
                metadata = self.files.get(rel_path)
                if metadata is None:
                    metadata = {
                        "history": [make_hash_entry(file_hash)],
                        "timestamp": datetime.fromtimestamp(st.st_mtime),
                    }
                    self.files[rel_path] = metadata
                elif current_hash(metadata) != file_hash:
                    metadata["history"].append(make_hash_entry(file_hash))
                    metadata["timestamp"] = datetime.fromtimestamp(st.st_mtime)
                else:
                    metadata["timestamp"] = datetime.fromtimestamp(st.st_mtime)

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

    def resolve_issue(self, path, issue_type=None):
        """Remove all unresolved issues for ``path`` (optionally of one type).

        Returns the number of removed issues.  Recomputing the file state is
        the caller's responsibility.
        """
        path = Path(path)
        kept = [
            issue
            for issue in self.issues
            if issue["path"] != path
            or (issue_type is not None and issue["type"] != issue_type)
        ]
        removed = len(self.issues) - len(kept)
        self.issues = kept
        return removed

    def has_issue(self, path, issue_types=None):
        """Whether ``path`` has an unresolved issue (optionally of given types)."""
        path = Path(path)
        for issue in self.issues:
            if issue["path"] != path:
                continue
            if issue_types is None or issue["type"] in issue_types:
                return True
        return False

    def is_faulty(self):
        """Whether the repository has any unresolved ``fault`` issue."""
        return any(issue["type"] == "fault" for issue in self.issues)

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

    def merge(self, other, remote=None):
        """Merge the peer manifest ``other`` into this (local) manifest.

        Implements the N/A/B/D merge table from docs/synchronization.md plus
        the quarantine rules from docs/manifest_implementation_task.md:

        * an unresolved issue on the *local* side for a path blocks every
          state change on that path (deletion is skipped when the local
          repository has an issue);
        * an unresolved issue on the *peer* side for a path blocks adopting
          (fetching) data from it (no data from faulty units);
        * a conflict is only recorded when neither side has an unresolved
          issue for the path (otherwise the merge waits for a human).

        ``remote`` is the ``"<address>:<port>"`` of the peer unit, recorded
        on conflict issues.  Returns a list of action dicts (one per path
        where a state change or a skip happened); never mutates ``other``.
        """
        if self.name != other.name:
            raise ValueError(
                f"cannot merge repositories with different names: "
                f"{self.name!r} vs {other.name!r}"
            )

        actions = []
        for rel_path in sorted(set(self.files) | set(other.files)):
            local_meta = self.files.get(rel_path)
            remote_meta = other.files.get(rel_path)
            local_issue = self.has_issue(rel_path)
            remote_issue = other.has_issue(rel_path)
            if not local_issue and not remote_issue:
                continue  # nothing to resolve on a clean path

            if local_meta is None:
                if remote_meta is None or is_deleted(remote_meta):
                    continue
                # N | A/B: fetch new file from peer
                if remote_issue:
                    actions.append(self._skip(rel_path, "peer quarantined"))
                else:
                    self._adopt(rel_path, remote_meta)
                    actions.append({"action": "adopted", "path": rel_path})
                continue

            if local_issue:
                # Any local issue quarantines the path: keep local state.
                actions.append(self._skip(rel_path, "local quarantined"))
                continue

            local_deleted = is_deleted(local_meta)
            remote_deleted = remote_meta is not None and is_deleted(remote_meta)

            if remote_meta is None:
                # local-only entry: do nothing
                continue
            if local_deleted and remote_deleted:
                # D | D: nothing, unless the peer deletion is older, then the
                # older token is appended so later recreations match.
                local_token = deletion_token(local_meta)
                remote_token = deletion_token(remote_meta)
                if (
                    remote_token is not None
                    and local_token is not None
                    and remote_token != local_token
                    and remote_token < local_token
                ):
                    local_meta["history"].append(remote_token)
                    actions.append(
                        {"action": "deletion_token_appended", "path": rel_path}
                    )
                continue
            if not local_deleted and not remote_deleted:
                # A | A / A | B
                local_hash = current_hash(local_meta)
                remote_hash = current_hash(remote_meta)
                if local_hash == remote_hash:
                    continue
                if local_hash in _history_hashes(remote_meta):
                    # local is behind: fetch the new version from peer
                    self._adopt(rel_path, remote_meta)
                    actions.append({"action": "adopted", "path": rel_path})
                else:
                    self._conflict(rel_path, remote)
                    actions.append({"action": "conflict", "path": rel_path})
                continue
            if local_deleted and not remote_deleted:
                # D | A/B: fetch only if the peer re-created the exact file we
                # deleted (identical deletion token in peer history).
                if deletion_token(local_meta) in remote_meta["history"]:
                    if remote_issue:
                        actions.append(self._skip(rel_path, "peer quarantined"))
                    else:
                        self._adopt(rel_path, remote_meta)
                        actions.append({"action": "adopted", "path": rel_path})
                else:
                    self._conflict(rel_path, remote)
                    actions.append({"action": "conflict", "path": rel_path})
                continue
            if not local_deleted and remote_deleted:
                # A | D: delete locally only if the peer saw our current
                # version before deleting it.
                if current_hash(local_meta) in _history_hashes(remote_meta):
                    local_meta["history"].append(make_deleted_entry())
                    actions.append({"action": "deleted", "path": rel_path})
                else:
                    self._conflict(rel_path, remote)
                    actions.append({"action": "conflict", "path": rel_path})
                continue

        return actions

    def _skip(self, rel_path, reason):
        return {"action": "skipped", "path": rel_path, "reason": reason}

    def _adopt(self, rel_path, remote_meta):
        self.files[rel_path] = deepcopy(remote_meta)

    def _conflict(self, rel_path, remote):
        self.add_issue(
            "conflict",
            self.path / rel_path,
            remote=remote,
            description="file state diverged between repositories",
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


def _history_hashes(metadata):
    """Set of hash values recorded in a metadata history."""
    return {
        entry[len(HASH_PREFIX):]
        for entry in metadata.get("history") or []
        if entry.startswith(HASH_PREFIX)
    }