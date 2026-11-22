#!/usr/bin/env python3
"""Beacon unit core script.

``unit.py`` is the core utility of the backup network (see the "Core
tools" section of the project readme).  The unit tracks its
repositories, synchronizes them with the other units of the network and
answers status requests (see the "Manifest and file synchronization
protocol" section of ``docs/software_architecture.md``).

On start up the script:

1. reads and validates the YAML config file (``unit_config.UnitConfig``);
2. loads the local manifest for every configured repository (an empty
   manifest is created when the file does not exist yet);
3. runs network discovery (see the "Network discovery protocol" section
   of ``docs/software_architecture.md`` and the :class:`Discovery`
   class): binds the UDP ``discovery_port`` (``root`` mode) or the
   first free port above it (``leaf`` mode, at most 128 ports scanned;
   if none is free the unit exits with an error), broadcasts an
   announcement to ``discovery_address:discovery_port`` and keeps
   track of the units that announced with the configured
   ``trusted_token``; a unit that goes silent is forgotten; discovery
   is skipped when the config does not set ``discovery_port``;
4. starts an HTTP server on the configured address and port that answers
   (message formats are defined by ``network_messages``):
   - ``GET /status`` -- JSON short status plus base64-encoded manifests, one
     per repository (unit x unit); the manifests are what a peer synchronizes
     from, so there is no separate manifest endpoint;
   - ``GET /file`` -- the raw content of one tracked file, the only request
     authenticated with the shared ``trusted_token`` (unit x unit);
   - ``GET /network`` -- JSON list of all units in the network (this unit
     plus any known units; unit x desktop_notify);
   - ``GET /summary`` -- the user facing HTML status page; ``GET /``
     redirects here;
   - ``POST /reevaluate`` -- re-evaluates the integrity issues of one
     repository (``repository=NAME`` given as a query parameter or a form
     field) and clears the faults that are no longer reproducible;
5. spawns three async tasks that run once immediately and then on the
   configured schedule:
   - the integrity check re-scans every repository through
     ``Manifest.update_from_directory`` (new files, changed hashes,
     deletions, I/O errors and hash changes without a modification time
     change all update the manifest and its issue list), saves it under
     ``<manifest_storage>/<repository name>.manifest`` and logs the
     result;
   - the synchronization asks every discovered unit for its status and for
     the manifests of the shared repositories, merges each manifest into
     the local one and fetches or removes the files the merged history
     calls for; a round that lands while the unit is busy is skipped and
     retried a minute later;
6. runs until it receives SIGINT or SIGTERM, then shuts down cleanly.

Usage:
    unit.py [--config FILE] [--log-level LEVEL | -v]

``--log-level`` takes ``debug``, ``info`` (the default), ``warning``
or ``error``; ``-v``/``--verbose`` is a shortcut for ``debug``.
"""

import argparse
import asyncio
import hashlib
import logging
import os
import signal
import socket
import stat
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import yaml

from manifest import (
    TEMP_SUFFIX,
    Manifest,
    current_hash,
    hash_file,
    is_deleted,
    make_hash_entry,
)
from network_messages import (
    FILE_TOKEN_HEADER,
    STATUS_FAULTY,
    STATUS_OK,
    compute_status,
    generate_discovery,
    generate_file_headers,
    generate_network_status,
    generate_status,
    generate_summary,
    manifest_status,
    parse_discovery,
    parse_file_headers,
    parse_file_target,
    parse_status,
)
from unit_config import (
    DEFAULT_MAX_PARALLEL_TRANSFERS,
    DEFAULT_TRANSFER_TIMEOUT,
    UnitConfig,
    validate_schedule,
)

logger = logging.getLogger("beacon.unit")

#: Maximum accepted HTTP request body size (bytes).
_MAX_BODY = 1024 * 1024

#: How far ``next_cron_match`` looks ahead; a schedule that does not match
#: within this window is reported as impossible.
_CRON_HORIZON_DAYS = 4 * 366

#: HTTP reason phrases used in the responses.
_STATUS_TEXTS = {
    200: "OK",
    303: "See Other",
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    409: "Conflict",
    410: "Gone",
    413: "Payload Too Large",
    415: "Unsupported Media Type",
    500: "Internal Server Error",
}

#: Query string names of the unit-to-unit requests.
_UNIT_QUERY = "unit"
_REPOSITORY_QUERY = "repository"
_PATH_QUERY = "path"

#: Seconds a synchronization round waits before it is tried again when it
#: landed while the unit was busy with another round or with an integrity
#: check (see the "Synchronization order" section of
#: ``docs/software_architecture.md``).
_BUSY_RETRY_DELAY = 60

#: Size of the chunks a ``file`` body is read or written in; the body is
#: streamed and never buffered, so a large file costs one chunk of memory.
_CHUNK_SIZE = 1024 * 1024


class TransferError(Exception):
    """A file transfer with a peer unit failed.

    Raised for a peer that cannot be reached or that refuses the request, for
    an answer that cannot be trusted (malformed or contradictory headers, a
    truncated body, content that does not match the expected hash) and for a
    failure to place the received file in the repository.  A synchronization
    round reports it, discards the merge that asked for the transfer and
    tries again on the next round with a fresh remote manifest.
    """


def _is_inside(root, path):
    """Whether ``path`` stays inside the ``root`` directory.

    Both sides are normalized to absolute paths without resolving symlinks,
    so a peer supplied relative path can never walk out of a repository with
    ``..`` segments (those are rejected earlier by
    ``network_messages.canonical_rel_path``) and a repository root configured
    with a trailing separator still contains its files.
    """
    root = os.path.abspath(str(root))
    full = os.path.abspath(str(path))
    return full == root or full.startswith(root.rstrip(os.sep) + os.sep)

#: Maximum number of ports a ``leaf`` unit scans above ``discovery_port``
#: looking for a free UDP discovery port; the unit exits with an error
#: message when no port in this range is free (see the "Network
#: discovery protocol" section of ``docs/software_architecture.md``).
_DISCOVERY_PORT_SCAN_LIMIT = 128

#: Seconds between discovery announcements.  Announcements double as
#: liveness signals: a unit that stopped announcing is forgotten after
#: it has been silent for ``expire_after`` seconds.
_DISCOVERY_ANNOUNCE_INTERVAL = 60.0

#: Address announcements are sent to when the config omits
#: ``discovery_address`` (the limited broadcast address).
_DISCOVERY_DEFAULT_ADDRESS = "255.255.255.255"

#: Fallback target of the routing table lookup that maps the addresses of
#: this machine (see :func:`_local_interface_addresses`); the lookup only
#: reads the routing table, nothing is ever sent to this address.
_DISCOVERY_ROUTE_PROBE = "8.8.8.8"


# --------------------------------------------------------------------- cron


def _parse_cron_fields(schedule):
    """Validate ``schedule`` and return ``(minute, hour, dom, month, dow)``.

    Each field is an integer or ``None`` for ``*``.  Raises ``ValueError``
    for an invalid schedule.
    """
    validate_schedule(schedule)
    return tuple(
        None if token == "*" else int(token) for token in schedule.split()
    )


def _day_matches(day, dom, dow):
    """Cron day matching; a Sunday is weekday 0.

    When both day-of-month and day-of-week are restricted (not ``*``) the
    day matches if either field matches (standard cron behaviour).
    """
    dom_ok = dom is None or day.day == dom
    dow_ok = dow is None or (day.weekday() + 1) % 7 == dow
    if dom is None:
        return dow_ok
    if dow is None:
        return dom_ok
    return dom_ok or dow_ok


def next_cron_match(schedule, after):
    """First local wall-clock minute matching ``schedule`` strictly after
    ``after``.

    ``schedule`` is a 5-field cron string (validated by
    ``unit_config.validate_schedule``).  Matching is done on the local wall
    clock with seconds and microseconds truncated.  Raises ``ValueError``
    for an invalid schedule or a schedule that never matches.
    """
    if not isinstance(after, datetime):
        raise ValueError(
            f"after must be a datetime, got {type(after).__name__}"
        )
    minute, hour, dom, month, dow = _parse_cron_fields(schedule)
    start = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    day = start.date()
    for _ in range(_CRON_HORIZON_DAYS):
        if (month is None or day.month == month) and _day_matches(
            day, dom, dow
        ):
            hours = (hour,) if hour is not None else range(24)
            minutes = (minute,) if minute is not None else range(60)
            for hour_value in hours:
                for minute_value in minutes:
                    candidate = datetime(
                        day.year, day.month, day.day, hour_value, minute_value
                    )
                    if candidate >= start:
                        return candidate
        day += timedelta(days=1)
    raise ValueError(
        f"schedule {schedule!r} does not match any date within "
        f"{_CRON_HORIZON_DAYS} days"
    )


# ------------------------------------------------------------- task looping


async def schedule_loop(action, delay=None, schedule=None, busy_check=None):
    """Run ``action`` once, then repeat according to the schedule.

    ``action`` is an async callable.  Exactly one of ``delay`` (seconds
    between runs) or ``schedule`` (5-field cron string) must be given;
    ``UnitConfig`` guarantees this for the unit's tasks.  A task that raises
    is logged and kept running: one failed round must not stop the unit from
    ever checking or synchronizing again.

    ``busy_check`` is an optional callable.  When it reports the unit busy at
    the moment a run is due, the run is skipped and retried
    ``_BUSY_RETRY_DELAY`` seconds later, so a scheduled round never stacks up
    behind a round that is still running (see the "Synchronization order"
    section of ``docs/software_architecture.md``).
    """
    if (delay is None) == (schedule is None):
        raise ValueError("exactly one of delay or schedule must be set")
    while True:
        if busy_check is not None and busy_check():
            logger.info(
                "scheduled task: skipped, the unit is busy; retrying in %d s",
                _BUSY_RETRY_DELAY,
            )
            await asyncio.sleep(_BUSY_RETRY_DELAY)
            continue
        try:
            await action()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("scheduled task failed; it will run again")
        if delay is not None:
            await asyncio.sleep(delay)
        else:
            now = datetime.now()
            wait = (next_cron_match(schedule, now) - now).total_seconds()
            await asyncio.sleep(max(0.0, wait))


# --------------------------------------------------------------------- unit


class Unit:
    """In-memory state and operations of a single Beacon unit.

    The state is the unit's identifier, the local manifest per configured
    repository, the start time and the list of known remotes (populated
    by network discovery, see :class:`Discovery`).  All mutations happen
    under ``Unit.lock`` so the HTTP handlers always see a consistent
    snapshot.

    Cross-unit transfers are bounded by ``max_parallel_transfers``:
    ``serving_slots`` limits how many ``file`` requests this unit answers at
    the same time, ``fetching_slots`` how many it runs towards peers.  The
    ``_sync_in_progress``/``_check_in_progress`` flags mark the unit busy so
    a scheduled synchronization is skipped instead of overlapping a round or
    an integrity check that is still running.
    """

    def __init__(self, config):
        self._config = config
        self._started = datetime.now()
        self._manifests = {}
        # name -> {"status": short status, "last_sync": datetime | None}
        self._remotes = {}
        self._lock = asyncio.Lock()
        self._discovery = None
        self._sync_in_progress = False
        self._check_in_progress = False
        # A config that leaves the transfer limits out gets the documented
        # default; resolving that once here keeps every use of them simple.
        self._transfer_timeout = (
            DEFAULT_TRANSFER_TIMEOUT
            if config.transfer_timeout is None
            else config.transfer_timeout
        )
        self._max_parallel_transfers = (
            DEFAULT_MAX_PARALLEL_TRANSFERS
            if config.max_parallel_transfers is None
            else config.max_parallel_transfers
        )
        self._serving_slots = asyncio.Semaphore(self._max_parallel_transfers)
        self._fetching_slots = asyncio.Semaphore(self._max_parallel_transfers)

    @property
    def transfer_timeout(self):
        """Seconds a peer may stay silent before a transfer is given up."""
        return self._transfer_timeout

    @property
    def max_parallel_transfers(self):
        """Number of ``file`` transfers this unit runs at the same time."""
        return self._max_parallel_transfers

    @property
    def serving_slots(self):
        """Semaphore bounding the ``file`` transfers served at once."""
        return self._serving_slots

    @property
    def fetching_slots(self):
        """Semaphore bounding the ``file`` transfers run towards peers."""
        return self._fetching_slots

    # ---------------------------------------------------------- properties

    @property
    def config(self):
        """The validated :class:`UnitConfig`."""
        return self._config

    @property
    def identifier(self):
        """Unit name; expected to be unique in the backup network."""
        return self._config.identifier

    @property
    def started(self):
        """Start time of this unit instance."""
        return self._started

    @property
    def uptime_seconds(self):
        """Whole seconds since this unit instance started."""
        return int((datetime.now() - self._started).total_seconds())

    @property
    def lock(self):
        """Lock guarding the manifest state."""
        return self._lock

    @property
    def manifests(self):
        """List of the local repository :class:`Manifest` objects."""
        return list(self._manifests.values())

    @property
    def remotes(self):
        """``(name, status, last sync time)`` tuples for known units.

        Populated by network discovery (:meth:`register_remote`); empty
        while discovery is disabled or no other unit has announced.
        """
        return [
            (name, info["status"], info["last_sync"])
            for name, info in sorted(self._remotes.items())
        ]

    def network_units(self):
        """``(name, status, last sync time)`` tuples for every unit in
        the network: this unit first (its live status, with the request
        time as the sync time since its data is reported directly), then
        the known units.
        """
        return [
            (self.identifier, self.overall_status(), datetime.now())
        ] + self.remotes

    def overall_status(self):
        """Short status of the unit: the most severe repository status."""
        return compute_status(self.manifests)

    # ----------------------------------------------------------- remote units

    def register_remote(self, name):
        """Register a unit discovered through network discovery.

        A freshly discovered unit carries the ``OK`` status and no
        synchronization time; the next synchronization round replaces both
        with what the peer's ``status`` answer reports
        (:meth:`Unit._report_remote`).  Returns ``True`` when the unit was
        not known before.
        """
        if name in self._remotes:
            return False
        self._remotes[name] = {"status": STATUS_OK, "last_sync": None}
        return True

    def forget_remote(self, name):
        """Remove a remote unit that went silent (no-op when unknown)."""
        self._remotes.pop(name, None)


    # ------------------------------------------------------- manifest state

    def manifest(self, name):
        """The manifest of repository ``name`` (``KeyError`` if unknown)."""
        try:
            return self._manifests[name]
        except KeyError:
            raise KeyError(f"unknown repository: {name!r}") from None

    def known_repositories(self):
        """Names of the repositories managed by this unit."""
        return list(self._manifests)

    def manifest_path(self, name):
        """Local path of the manifest file of repository ``name``."""
        return Path(self._config.manifest_storage) / f"{name}.manifest"

    def repo_path(self, name):
        """Local root directory of repository ``name``."""
        for repo in self._config.repository:
            if repo.name == name:
                return Path(repo.path)
        raise KeyError(f"unknown repository: {name!r}")

    def load_manifests(self):
        """Load the manifest of every configured repository.

        A missing manifest file yields an empty manifest for the
        repository; the first integrity check fills it in.  Raises
        ``ValueError`` for a corrupt or unsupported manifest file.
        """
        for repo in self._config.repository:
            path = self.manifest_path(repo.name)
            if path.exists():
                manifest = Manifest.load(path)
            else:
                manifest = Manifest(
                    repo.name, repo.path, unit_id=self._config.identifier
                )
            self._manifests[repo.name] = manifest

    # ------------------------------------------------- integrity checking

    async def integrity_check(self):
        """Re-scan every repository, refresh and save its manifest.

        The scan runs in a worker thread (hashing is blocking I/O) while
        the unit lock is held, so concurrent HTTP handlers never observe a
        half-updated manifest.  A repository whose directory is missing is
        skipped with an error; a scan failure is logged and the remaining
        repositories are still checked.  While the scan runs the unit is
        busy: a synchronization round scheduled for that moment is skipped.
        """
        self._check_in_progress = True
        try:
            async with self._lock:
                await asyncio.to_thread(self._run_integrity_check)
        finally:
            self._check_in_progress = False

    def _run_integrity_check(self):
        """Scan and refresh every repository (the body of the check task)."""
        for name in self.known_repositories():
            try:
                status = self._check_repository(name)
            except Exception:
                logger.exception(
                    "integrity check failed for repository %r", name
                )
                continue
            if status is None:
                continue
            logger.info(
                "integrity: repository %r: %d file(s) (%d active, "
                "%d deleted), status: %s",
                name,
                status["total_files"],
                status["active_files"],
                status["deleted_files"],
                manifest_status(self._manifests[name]),
            )
            for issue in status["issues"]:
                logger.warning(
                    "  %s [%s]: %s",
                    issue["type"],
                    issue["path"],
                    issue["description"],
                )

    def _check_repository(self, name):
        """Scan one repository, refresh and save its manifest.

        Returns the manifest's ``status()`` dict, or ``None`` when the
        repository directory is missing.
        """
        manifest = self._manifests[name]
        root = self.repo_path(name)
        if not root.is_dir():
            logger.error(
                "repository %r: directory %s does not exist or is not a "
                "directory; skipping",
                name,
                root,
            )
            return None
        manifest.update_from_directory(root)
        manifest.save(self.manifest_path(name))
        return manifest.status()


    # ----------------------------------------------------------- reevaluate

    async def reevaluate_repository(self, name):
        """Re-check the fault issues of one repository.

        A fault is cleared when the file can be read again and either
        matches the tracked hash or its modification time has moved on
        (the new version is then appended to the history).  Faults that
        are still reproducible are kept.  Returns the number of resolved
        issues.
        """
        async with self._lock:
            return await asyncio.to_thread(self._reevaluate_repository, name)

    def _reevaluate_repository(self, name):
        manifest = self.manifest(name)
        root = self.repo_path(name)
        if not root.is_dir():
            raise ValueError(f"not a directory: {root}")
        resolved = 0
        for issue in [
            issue for issue in manifest.issues if issue["type"] == "fault"
        ]:
            if self._fault_resolved(manifest, root, issue):
                manifest.resolve_issue(issue["path"], "fault")
                resolved += 1
        manifest.save(self.manifest_path(name))
        if resolved:
            logger.info(
                "reevaluate: repository %r: %d fault issue(s) resolved",
                name,
                resolved,
            )
        return resolved

    @staticmethod
    def _fault_resolved(manifest, root, issue):
        """Whether the fault ``issue`` is no longer reproducible.

        Accepts newly readable files into the manifest; a file whose
        content differs from the tracked one but whose modification time
        moved on is accepted as a new version.
        """
        rel_path = Path(issue["path"]).as_posix()
        full_path = root / rel_path
        try:
            st = os.lstat(full_path)
        except OSError:
            return False
        if not stat.S_ISREG(st.st_mode):
            return False
        try:
            file_hash = hash_file(full_path)
        except OSError:
            return False
        metadata = manifest.files.get(rel_path)
        new_timestamp = datetime.fromtimestamp(st.st_mtime)
        if metadata is None:
            manifest.files[rel_path] = {
                "history": [make_hash_entry(file_hash)],
                "timestamp": new_timestamp,
            }
            return True
        if file_hash == current_hash(metadata):
            return True
        if new_timestamp == metadata["timestamp"]:
            return False
        metadata["history"].append(make_hash_entry(file_hash))
        metadata["timestamp"] = new_timestamp
        return True

    # -------------------------------------------------------- synchronization

    @property
    def is_busy(self):
        """True while a synchronization round or an integrity check runs."""
        return self._sync_in_progress or self._check_in_progress

    def set_discovery(self, discovery):
        """Attach the :class:`Discovery` instance that knows peer addresses."""
        self._discovery = discovery

    def peer_target(self, unit_id):
        """``(address, tcp_port)`` of a peer, or ``None`` when it is unknown."""
        if self._discovery is None:
            return None
        return self._discovery.peer_target(unit_id)

    def _report_remote(self, unit_id, status):
        """Record what a synchronization round learned about a peer unit."""
        info = self._remotes.setdefault(
            unit_id, {"status": STATUS_OK, "last_sync": None}
        )
        info["status"] = status
        info["last_sync"] = datetime.now()

    async def sync(self, reason="scheduled"):
        """Synchronize the repositories with the peer units once.

        One round asks every known unit for its ``status`` answer (its short
        status plus the manifests of the repositories it tracks), merges each
        shared repository into the local manifest and carries out the
        transfers the merged history calls for.  A peer or a repository that
        reports issues is read but never used to change local data (see the
        "Algorithm" section of ``docs/synchronization.md``).

        Returns a summary dict of the round, or ``None`` when the round was
        skipped because the unit is busy; the scheduler retries it shortly.
        """
        if self._sync_in_progress:
            logger.info(
                "synchronization (%s): skipped, a round is already running",
                reason,
            )
            return None
        if self._check_in_progress:
            logger.info(
                "synchronization (%s): skipped, an integrity check is running",
                reason,
            )
            return None
        self._sync_in_progress = True
        try:
            return await self._sync_round(reason)
        finally:
            self._sync_in_progress = False

    async def _sync_round(self, reason):
        """Synchronize with every known peer; returns the round summary."""
        repositories = self.known_repositories()
        peers = sorted(
            name for name in self._remotes if name != self.identifier
        )
        summary = {
            "reason": reason,
            "peers": len(peers),
            "fetches": 0,
            "deletions": 0,
            "failures": 0,
        }
        if not peers:
            logger.info(
                "synchronization (%s): no known peers; %d repository(ies) "
                "unchanged",
                reason,
                len(repositories),
            )
            return summary
        logger.info(
            "synchronization (%s): %d peer(s) (%s), %d repository(ies)",
            reason,
            len(peers),
            ", ".join(peers),
            len(repositories),
        )
        for unit_id in peers:
            stats = await self._sync_with_peer(unit_id, repositories)
            for key in ("fetches", "deletions", "failures"):
                summary[key] += stats[key]
        logger.info(
            "synchronization (%s) done: %d file(s) fetched, %d removed, "
            "%d failure(s)",
            reason,
            summary["fetches"],
            summary["deletions"],
            summary["failures"],
        )
        return summary

    async def _sync_with_peer(self, unit_id, repositories):
        """Synchronize every shared repository with one peer unit."""
        stats = {"fetches": 0, "deletions": 0, "failures": 0}
        try:
            status, remote_manifests = await self._request_status(unit_id)
        except TransferError as error:
            logger.warning("synchronization: %s", error)
            return stats
        self._report_remote(unit_id, status)
        remote_by_name = {manifest.name: manifest for manifest in remote_manifests}
        for name in repositories:
            remote = remote_by_name.get(name)
            if remote is None:
                continue  # this peer does not track the repository
            reason = self._sync_blocker(name, remote)
            if reason is not None:
                logger.info(
                    "synchronization: repository %r is not synchronized "
                    "with unit %r: %s",
                    name,
                    unit_id,
                    reason,
                )
                continue
            repo_stats = await self._sync_repository(unit_id, name, remote)
            for key in stats:
                stats[key] += repo_stats[key]
        return stats

    def _sync_blocker(self, name, remote):
        """Why repository ``name`` must not be merged with ``remote``.

        Returns ``None`` when the merge may go ahead.  A repository with
        issues on either side is left alone: its data may be damaged or
        incomplete, and a merge could turn a hardware problem into a lost
        file (see the "Algorithm" section of ``docs/synchronization.md``).
        A peer that answers with the manifest of a faulty repository is
        treated the same way: its issues are visible in the answer.
        """
        try:
            local = self.manifest(name)
        except KeyError:
            return "this unit does not track it"
        remote_status = manifest_status(remote)
        if remote_status != STATUS_OK:
            return f"unit {remote.unit_id!r} reports it as {remote_status}"
        local_status = manifest_status(local)
        if local_status != STATUS_OK:
            return f"it is {local_status} on this unit"
        return None

    async def _sync_repository(self, unit_id, name, remote):
        """Merge one remote manifest into the local one and apply the result.

        The merge itself is fast and runs under the unit lock; the transfers
        it asks for run outside of the lock so that a slow download does not
        hold up the HTTP handlers.  A transfer that fails aborts the
        repository: the merged manifest is discarded and reloaded from disk,
        so the next round recomputes its actions from a record that matches
        the files on disk instead of keeping a history that promises bytes
        which never arrived.
        """
        stats = {"fetches": 0, "deletions": 0, "failures": 0}
        async with self._lock:
            manifest = self.manifest(name)
            manifest.clean_remote_issues(unit_id)
            actions = manifest.merge(remote, remote_id=unit_id)
            # the hash each fetch must reproduce, read from the merged history
            expected = {
                action["path"]: current_hash(manifest.files[action["path"]])
                for action in actions
                if action["action"] == "fetch"
            }
            if not actions:
                manifest.save(self.manifest_path(name))
                return stats
        failed = None
        for action in actions:
            rel_path = action["path"]
            try:
                if action["action"] == "fetch":
                    await self._fetch_file(
                        unit_id, name, rel_path, expected[rel_path]
                    )
                    stats["fetches"] += 1
                else:
                    self._remove_local_file(name, rel_path)
                    stats["deletions"] += 1
            except TransferError as error:
                stats["failures"] += 1
                failed = error
                logger.warning(
                    "synchronization: cannot %s %r of repository %r "
                    "(unit %r): %s",
                    action["action"],
                    rel_path,
                    name,
                    unit_id,
                    error,
                )
                break
        async with self._lock:
            if failed is None:
                self.manifest(name).save(self.manifest_path(name))
            else:
                self._reload_manifest(name)
        return stats

    def _reload_manifest(self, name):
        """Throw away an in-memory manifest in favour of the stored one."""
        path = self.manifest_path(name)
        try:
            self._manifests[name] = Manifest.load(path)
        except (OSError, ValueError) as error:
            logger.error(
                "synchronization: cannot reload the manifest of repository "
                "%r from %s: %s",
                name,
                path,
                error,
            )
            return
        logger.info(
            "synchronization: repository %r is left unchanged and will be "
            "retried with a fresh manifest of the peer unit",
            name,
        )

    def _remove_local_file(self, name, rel_path):
        """Delete one tracked file whose merged history records a deletion."""
        root = self.repo_path(name)
        full_path = root / rel_path
        if not _is_inside(root, full_path):
            raise TransferError(
                f"{rel_path!r} does not stay inside repository {name!r}"
            )
        try:
            full_path.unlink(missing_ok=True)
        except OSError as error:
            raise TransferError(f"cannot delete {full_path}: {error}") from error

    async def _fetch_file(self, unit_id, name, rel_path, expected_hash):
        """Fetch the body of one tracked file from a peer unit.

        The bytes are streamed into ``<target><TEMP_SUFFIX>`` next to the
        target (a name the manifest scan never tracks), verified against the
        announced size and against the hash the remote manifest recorded,
        given the access mode and the ownership this unit is configured with,
        and only then renamed onto the target: a file inside a repository is
        always a complete version.  Whatever fails, the temporary file is
        removed and the target stays as it was.

        Raises :class:`TransferError` with the reason the file did not arrive.
        """
        target = self.peer_target(unit_id)
        if target is None:
            raise TransferError(f"unit {unit_id!r} has no known address")
        address, tcp_port = target
        root = self.repo_path(name)
        full_path = root / rel_path
        if not _is_inside(root, full_path):
            raise TransferError(
                f"{rel_path!r} does not stay inside repository {name!r}"
            )
        part_path = full_path.with_name(full_path.name + TEMP_SUFFIX)
        if part_path.exists():
            raise TransferError(f"{rel_path!r} is already being downloaded")
        timeout = self.transfer_timeout
        query = urlencode({_REPOSITORY_QUERY: name, _PATH_QUERY: rel_path})
        writer = None
        try:
            async with self._fetching_slots:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(address, tcp_port), timeout
                )
                writer.write(
                    _request_head(
                        f"/file?{query}",
                        self._config.trusted_token,
                    )
                )
                await writer.drain()
                status, reason, head = await asyncio.wait_for(
                    _read_response_head(reader), timeout
                )
                if status != 200:
                    detail = await asyncio.wait_for(reader.read(400), timeout)
                    raise TransferError(
                        f"unit {unit_id!r} refused {name}/{rel_path}: "
                        f"{status} {reason} "
                        f"{detail.decode('utf-8', errors='replace').strip()}"
                    )
                file_hash, size, serving_unit = parse_file_headers(head)
                if serving_unit != unit_id:
                    logger.warning(
                        "synchronization: the answer for %r came from unit "
                        "%r, not from the expected %r",
                        rel_path,
                        serving_unit,
                        unit_id,
                    )
                if file_hash != expected_hash:
                    # the peer moved on since its manifest was fetched: the
                    # body would be a version no history records.
                    raise TransferError(
                        f"unit {unit_id!r} serves hash {file_hash} of "
                        f"{rel_path!r}, the manifest expects {expected_hash}"
                    )
                full_path.parent.mkdir(parents=True, exist_ok=True)
                written, digest = await _stream_into_file(
                    reader,
                    part_path,
                    size,
                    timeout,
                    self._config.max_transfer_size,
                )
                if written != size:
                    raise TransferError(
                        f"unit {unit_id!r} sent {written} of the {size} "
                        f"bytes of {rel_path!r} it announced"
                    )
                if digest.hexdigest() != file_hash:
                    raise TransferError(
                        f"the content of {rel_path!r} does not match the hash "
                        f"{file_hash} of unit {unit_id!r}"
                    )
                await asyncio.to_thread(
                    _publish_fetched_file, self._config, part_path, full_path
                )
        except TransferError:
            part_path.unlink(missing_ok=True)
            raise
        except TimeoutError as error:
            part_path.unlink(missing_ok=True)
            raise TransferError(
                f"the transfer of {rel_path!r} from unit {unit_id!r} stayed "
                f"idle for {timeout} s"
            ) from error
        except (
            OSError,
            ValueError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
        ) as error:
            part_path.unlink(missing_ok=True)
            raise TransferError(
                f"cannot fetch {rel_path!r} from unit {unit_id!r}: {error}"
            ) from error
        finally:
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except (ConnectionError, OSError):
                    pass
        logger.info(
            "synchronization: fetched %r of repository %r from unit %r "
            "(%d bytes)",
            rel_path,
            name,
            unit_id,
            written,
        )

    async def _request_status(self, unit_id):
        """The ``(short status, manifests)`` pair a peer unit reports."""
        payload = await self._request_peer_data(unit_id, "/status", {})
        try:
            return parse_status(payload)
        except ValueError as error:
            raise TransferError(
                f"unit {unit_id!r} answered with an unreadable status: {error}"
            ) from error

    async def _request_peer_data(self, unit_id, path, query):
        """One plain ``GET`` to a peer unit; returns its body as text.

        Used for the small, fully buffered answers of the unit-to-unit
        protocol (``status``); the body of a ``file`` answer is streamed
        instead.  Raises :class:`TransferError` for a peer that cannot be
        reached or that does not answer with ``200 OK``.
        """
        target = self.peer_target(unit_id)
        if target is None:
            raise TransferError(f"unit {unit_id!r} has no known address")
        address, tcp_port = target
        timeout = self.transfer_timeout
        params = dict(query)
        params[_UNIT_QUERY] = self.identifier
        writer = None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(address, tcp_port), timeout
            )
            writer.write(
                _request_head(f"{path}?{urlencode(params)}")
            )
            await writer.drain()
            status, reason, head = await asyncio.wait_for(
                _read_response_head(reader), timeout
            )
            if status != 200:
                raise TransferError(
                    f"unit {unit_id!r} answered {path} with {status} {reason}"
                )
            body = await _read_body(reader, head, timeout)
            return body.decode("utf-8", errors="replace")
        except TransferError:
            raise
        except TimeoutError as error:
            raise TransferError(
                f"unit {unit_id!r} stayed silent for {timeout} s"
            ) from error
        except (
            OSError,
            ValueError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
        ) as error:
            raise TransferError(
                f"cannot reach unit {unit_id!r}: {error}"
            ) from error
        finally:
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except (ConnectionError, OSError):
                    pass

    async def serve_file(self, writer, method, query, headers):
        """Answer one ``file`` request by streaming the tracked file.

        The request never changes the state of this unit: no repository is
        rescanned and no manifest is written.  Three independent guards
        decide whether the content may leave: the peer proves that it belongs
        to the backup network with the shared token, the path must be tracked
        by the manifest of the repository (which rules out anything this unit
        does not consider its own data), and the resolved path must stay
        inside the repository root.  A refusal is a status code and one line
        of text, a success is the raw content preceded by its hash, its size
        and the name of this unit: the receiving unit decides the file system
        properties of the file it writes itself.
        """
        if method != "GET":
            await _write_text_response(writer, 405, "method not allowed")
            return
        try:
            repository, rel_path = parse_file_target(query)
        except ValueError as error:
            await _write_text_response(writer, 400, str(error))
            return
        token = headers.get(FILE_TOKEN_HEADER.lower(), "")
        if token != self._config.trusted_token:
            await _write_text_response(
                writer, 403, f"a matching {FILE_TOKEN_HEADER} header is required"
            )
            return
        try:
            manifest = self.manifest(repository)
        except KeyError:
            await _write_text_response(
                writer, 404, f"unknown repository: {repository}"
            )
            return
        root = self.repo_path(repository)
        full_path = root / rel_path
        if not _is_inside(root, full_path):
            await _write_text_response(
                writer,
                403,
                f"{rel_path} resolves outside repository {repository}",
            )
            return
        version = manifest.files.get(rel_path)
        if version is None:
            await _write_text_response(
                writer,
                404,
                f"{rel_path} is not tracked in repository {repository}",
            )
            return
        if is_deleted(version):
            await _write_text_response(
                writer,
                410,
                f"{rel_path} is recorded as deleted in repository {repository}",
            )
            return
        if manifest_status(manifest) == STATUS_FAULTY:
            await _write_text_response(
                writer, 409, f"repository {repository} is faulty"
            )
            return
        file_hash = current_hash(version)
        if file_hash is None:
            await _write_text_response(
                writer,
                404,
                f"{rel_path} has no version this unit can serve",
            )
            return
        size, refusal = await asyncio.to_thread(
            _check_served_file,
            full_path,
            file_hash,
            version["timestamp"],
            self._config.max_transfer_size,
        )
        if refusal is not None:
            status, message = refusal
            await _write_text_response(writer, status, message)
            return
        async with self._serving_slots:
            await _serve_head(
                writer,
                200,
                "application/octet-stream",
                generate_file_headers(self.identifier, file_hash, size),
            )
            try:
                sent = await _stream_file(writer, full_path)
            except (ConnectionError, OSError) as error:
                # the peer is gone; the announced size was not reached, which
                # is what the requester checks before it accepts the file.
                logger.warning(
                    "serving %r of repository %r was interrupted: %s",
                    rel_path,
                    repository,
                    error,
                )
                return
        logger.info(
            "served %r of repository %r (%d bytes)", rel_path, repository, sent
        )

    # ---------------------------------------------------------- HTTP handler

    async def handle_http(self, method, path, query, body):
        """Answer one HTTP request.

        ``query`` is the parsed query string (a dict of lists) and ``body``
        the raw request body (bytes).  Returns a
        ``(status code, content type, body)`` tuple.
        """
        try:
            if method not in ("GET", "POST"):
                return 405, "text/plain; charset=utf-8", "method not allowed\n"
            if path in ("/", "/status", "/network", "/summary", "/reevaluate"):
                expected = "POST" if path == "/reevaluate" else "GET"
                if method != expected:
                    return 405, "text/plain; charset=utf-8", "method not allowed\n"
            if path == "/":
                return 303, None, "/summary"
            if path == "/status":
                async with self._lock:
                    payload = generate_status(self.manifests)
                return 200, "application/json", payload
            if path == "/network":
                async with self._lock:
                    payload = generate_network_status(self.network_units())
                return 200, "application/json", payload
            if path == "/summary":
                async with self._lock:
                    payload = generate_summary(
                        self.identifier,
                        self.uptime_seconds,
                        self.manifests,
                        self.remotes,
                    )
                return 200, "text/html; charset=utf-8", payload
            if path == "/reevaluate":
                repository = _first_value(query.get("repository"))
                if repository is None and body:
                    repository = _first_value(
                        parse_qs(
                            body.decode("utf-8", errors="replace")
                        ).get("repository")
                    )
                if repository is None:
                    return (
                        400,
                        "text/plain; charset=utf-8",
                        "missing 'repository' parameter\n",
                    )
                if repository not in self._manifests:
                    return (
                        404,
                        "text/plain; charset=utf-8",
                        f"unknown repository: {repository}\n",
                    )
                resolved = await self.reevaluate_repository(repository)
                return (
                    200,
                    "text/plain; charset=utf-8",
                    f"repository {repository!r} re-evaluated: "
                    f"{resolved} fault issue(s) resolved\n",
                )
        except KeyError as error:
            return 404, "text/plain; charset=utf-8", f"{error.args[0]}\n"
        except ValueError as error:
            return 500, "text/plain; charset=utf-8", f"{error}\n"
        except Exception:
            logger.exception("error handling %s %s", method, path)
            return (
                500,
                "text/plain; charset=utf-8",
                "internal server error\n",
            )
        return 404, "text/plain; charset=utf-8", "not found\n"


def _first_value(values):
    """The first element of a ``parse_qs`` value list, or ``None``."""
    return values[0] if values else None


# ------------------------------------------------------------ network discovery


def _is_loopback_address(address):
    """Whether an IPv4 address belongs to the loopback network."""
    return address.startswith("127.")


def _outbound_source_address(target):
    """The local IPv4 address used to reach ``target``, ``None`` if none.

    ``connect`` on a connectionless UDP socket only consults the routing
    table: no packet leaves the machine and no peer is involved.  It does
    fail for addresses that cannot be route targets, a broadcast address
    among them.
    """
    if not target:
        return None
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect((target, 1))
            return sock.getsockname()[0]
    except OSError:
        return None


def _route_probe_targets(discovery_address):
    """The addresses to look up in the routing table to map this machine.

    ``discovery_address`` describes the network the unit announces to, so
    the interface that serves it is one of the local ones.  A broadcast
    address itself cannot be a ``connect`` target, so the first address of
    its network is probed instead; the lookup succeeds whether or not
    anything answers there.  ``_DISCOVERY_ROUTE_PROBE`` covers the default
    route when nothing better is configured.
    """
    targets = []
    if discovery_address:
        targets.append(discovery_address)
        network, _, last = discovery_address.rpartition(".")
        if network and last == "255":
            targets.append(f"{network}.1")
    targets.append(_DISCOVERY_ROUTE_PROBE)
    return targets


def _local_interface_addresses(discovery_address=None):
    """Best-effort set of the IPv4 addresses of this machine.

    An announcement arriving through a LAN broadcast carries the address
    of the interface it was sent from, which is usually not the address
    the hostname of the machine resolves to.  The routing table entries
    leading to the discovery network are therefore collected next to the
    hostname addresses: without them a second unit of the same machine
    looks like a remote one and a root never forwards to it.
    """
    addresses = {"127.0.0.1"}
    hostname = socket.gethostname()
    try:
        infos = socket.getaddrinfo(hostname, None, socket.AF_INET)
    except OSError:
        logger.warning(
            "discovery: cannot resolve the hostname %r, relying on the "
            "routing table for the local interface addresses",
            hostname,
        )
        infos = []
    for info in infos:
        addresses.add(info[4][0])
    for target in _route_probe_targets(discovery_address):
        source = _outbound_source_address(target)
        if source is None:
            logger.debug("discovery: no local route to probe %s", target)
            continue
        addresses.add(source)
    return addresses


class _DiscoveryProtocol(asyncio.DatagramProtocol):
    """Feeds received UDP datagrams into :meth:`Discovery.handle_datagram`."""

    def __init__(self, discovery):
        self._discovery = discovery

    def datagram_received(self, data, addr):
        self._discovery.handle_datagram(data, addr)

    def error_received(self, exc):
        # For instance an ICMP "port unreachable" after a peer went away:
        # the next announcement either reaches the peer again or it expires.
        logger.debug("discovery: UDP error: %s", exc)


class Discovery:
    """UDP network discovery of one unit (see the "Network discovery
    protocol" section of ``docs/software_architecture.md``).

    Mode selection on :meth:`start`: the unit first tries to bind the
    UDP ``discovery_port``; when the port is taken (by another unit on
    the same machine) it scans the following ports one by one and binds
    the first free one (``leaf`` mode, at most ``port_scan_limit``
    attempts, then it gives up with an error).  A ``root`` unit listens
    on the shared discovery port and resends every announcement it
    receives to the known units of its own machine (the units whose
    announcements arrive from the loopback network or from an address of
    this machine, taken from the routing table and learned from the unit's
    own echoed broadcast); a ``leaf`` only listens
    on its own offset port and learns the rest of the network through
    the forwarding root.  The offset ports are what lets several units
    share one machine without fighting over one UDP port.

    After binding, and then every ``announce_interval`` seconds, the
    unit broadcasts an announcement (``network_messages.generate_
    discovery``: unit name, discovery UDP port, TCP port and trusted
    token) to ``discovery_address:discovery_port``.  A root
    additionally re-sends its own announcement to its known local
    units: leaves do not listen on the shared port, so without the
    re-send they could never learn that their local root exists.

    A received announcement is only acted upon when its trusted token
    equals the local one; the announcing unit is then registered as a
    remote of the owning unit.  Every announcement doubles as a
    liveness signal: a unit that stays silent for ``expire_after``
    seconds is forgotten again.
    """

    def __init__(
        self,
        unit,
        announce_interval=_DISCOVERY_ANNOUNCE_INTERVAL,
        expire_after=None,
        port_scan_limit=_DISCOVERY_PORT_SCAN_LIMIT,
        local_addresses=None,
    ):
        if announce_interval <= 0:
            raise ValueError("announce_interval must be positive")
        if expire_after is None:
            expire_after = 3 * announce_interval
        if expire_after <= 0:
            raise ValueError("expire_after must be positive")
        if port_scan_limit < 1:
            raise ValueError("port_scan_limit must be at least 1")
        self._unit = unit
        self._config = unit.config
        self._announce_interval = float(announce_interval)
        self._expire_after = float(expire_after)
        self._port_scan_limit = int(port_scan_limit)
        self._local_addresses = (
            set(local_addresses)
            if local_addresses is not None
            else _local_interface_addresses(self._config.discovery_address)
        )
        # Addresses of this machine recognized at runtime, when one of the
        # unit's own announcements comes back through a broadcast; kept
        # apart from the addresses detected at startup.
        self._learned_addresses = set()
        self._mode = None
        self._udp_port = None
        self._transport = None
        self._task = None
        # unit id -> {address, unit_port, tcp_port, local, last_seen}
        self._peers = {}

    # -------------------------------------------------------------- properties

    @property
    def mode(self):
        """``"root"`` or ``"leaf"`` (``None`` before :meth:`start`)."""
        return self._mode

    @property
    def udp_port(self):
        """The bound UDP discovery port (``None`` before :meth:`start`)."""
        return self._udp_port

    def known_units(self):
        """``(unit id, address, UDP port, TCP port, local)`` tuples for the
        known peers, sorted by unit id.

        ``address`` is the address the peer announced from (where the
        root resends to); ``local`` marks the peers of the own machine,
        the root's forwarding targets.
        """
        return [
            (
                unit_id,
                peer["address"],
                peer["unit_port"],
                peer["tcp_port"],
                peer["local"],
            )
            for unit_id, peer in sorted(self._peers.items())
        ]

    def peer_target(self, unit_id):
        """``(address, tcp_port)`` to reach ``unit_id`` over TCP.

        The address is the one the peer sent its announcements from, which is
        the address its HTTP server listens on.  ``None`` is returned when the
        peer is not (or no longer) known, or announced without a TCP port -- a
        unit without a server address cannot be synchronized with.
        """
        peer = self._peers.get(unit_id)
        if peer is None or peer["tcp_port"] is None:
            return None
        return peer["address"], peer["tcp_port"]

    # ------------------------------------------------------------ start / stop

    async def start(self):
        """Bind the discovery UDP port, announce the unit and start the loop.

        Raises ``ValueError`` when the config has no ``discovery_port``
        and ``OSError`` when no free UDP port was found (the unit exits
        with an error message in that case).
        """
        if self._transport is not None:
            raise RuntimeError("discovery is already started")
        base_port = self._config.discovery_port
        if base_port is None:
            raise ValueError("discovery_port is not configured")
        sock, mode = self._bind_discovery_socket(base_port)
        try:
            transport, _ = (
                await asyncio.get_running_loop().create_datagram_endpoint(
                    lambda: _DiscoveryProtocol(self), sock=sock
                )
            )
        except Exception:
            sock.close()
            raise
        self._mode = mode
        self._udp_port = sock.getsockname()[1]
        self._transport = transport
        logger.info(
            "discovery: %s mode, listening on UDP port %d",
            mode,
            self._udp_port,
        )
        logger.debug(
            "discovery: local interface addresses: %s (announcements coming "
            "from one of them are units of this machine)",
            ", ".join(sorted(self._local_addresses)) or "none",
        )
        await self.announce()
        self._task = asyncio.create_task(
            self._announce_loop(), name="discovery-announce"
        )

    async def stop(self):
        """Cancel the announcement loop and close the UDP socket."""
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._transport is not None:
            self._transport.close()
            self._transport = None

    def _bind_discovery_socket(self, base_port):
        """Bind ``base_port`` (root) or scan the following ports (leaf).

        Returns a ``(socket, mode)`` tuple; raises ``OSError`` when
        neither the base port nor the following ``port_scan_limit``
        ports are free.
        """
        try:
            return self._try_bind(base_port), "root"
        except OSError:
            logger.info(
                "discovery: UDP port %d is taken, scanning for a free "
                "port (leaf mode)",
                base_port,
            )
        for offset in range(1, self._port_scan_limit + 1):
            port = base_port + offset
            if port > 65535:
                break
            try:
                return self._try_bind(port), "leaf"
            except OSError:
                continue
        raise OSError(
            f"no free UDP discovery port: tried port {base_port} and "
            f"the next {self._port_scan_limit} ports"
        )

    @staticmethod
    def _try_bind(port):
        """A bound non-blocking UDP socket on ``port`` (``OSError`` when
        the port is taken)."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind(("0.0.0.0", port))
            sock.setblocking(False)
        except OSError:
            sock.close()
            raise
        return sock

    # ----------------------------------------------------------------- sending

    async def announce(self):
        """Broadcast this unit's announcement; a root also re-sends it to
        its local peers (see :meth:`_forward`)."""
        message = generate_discovery(
            self._unit.identifier,
            self._udp_port,
            self._config.port,
            self._config.trusted_token,
        ).encode("utf-8")
        address = self._config.discovery_address or _DISCOVERY_DEFAULT_ADDRESS
        logger.debug(
            "discovery: announcing unit %r (UDP port %s, TCP port %s) to "
            "%s:%s in %s mode",
            self._unit.identifier,
            self._udp_port,
            self._config.port,
            address,
            self._config.discovery_port,
            self._mode,
        )
        try:
            self._transport.sendto(
                message, (address, self._config.discovery_port)
            )
        except OSError as error:
            logger.warning(
                "discovery: cannot announce to %s:%d: %s",
                address,
                self._config.discovery_port,
                error,
            )
        self._forward(message, None, None)

    def _forward(self, message, origin_addr, origin_unit_id):
        """Resend one announcement to the known local units (root only).

        The unit the announcement came from (``origin_unit_id`` bound to
        ``origin_addr``) is never sent its own announcement back.
        """
        if self._mode != "root":
            logger.debug(
                "discovery: nothing forwarded, this unit is a %s", self._mode
            )
            return
        source = (
            "its own announcement"
            if origin_addr is None
            else "%s:%s" % (origin_addr[0], origin_addr[1])
        )
        forwarded = 0
        for unit_id, peer in list(self._peers.items()):
            if not peer["local"]:
                logger.debug(
                    "discovery: not forwarding to unit %r: not on this host",
                    unit_id,
                )
                continue
            if unit_id == origin_unit_id or (
                origin_addr is not None
                and tuple(origin_addr[:2])
                == (peer["address"], peer["unit_port"])
            ):
                logger.debug(
                    "discovery: not forwarding to unit %r: the announcement "
                    "came from it at %s",
                    unit_id,
                    source,
                )
                continue
            try:
                self._transport.sendto(
                    message, (peer["address"], peer["unit_port"])
                )
            except OSError as error:
                logger.debug(
                    "discovery: cannot forward to unit %r at %s:%d: %s",
                    unit_id,
                    peer["address"],
                    peer["unit_port"],
                    error,
                )
                continue
            forwarded += 1
            logger.debug(
                "discovery: forwarded to unit %r at %s:%d",
                unit_id,
                peer["address"],
                peer["unit_port"],
            )
        logger.debug(
            "discovery: forwarded %s to %d of %d known peer(s)",
            source,
            forwarded,
            len(self._peers),
        )


    # --------------------------------------------------------------- receiving

    def handle_datagram(self, data, addr):
        """Process one received UDP datagram (a peer announcement)."""
        logger.debug(
            "discovery: %d byte datagram from %s:%s", len(data), *addr
        )
        try:
            message = data.decode("utf-8")
        except UnicodeDecodeError:
            logger.debug(
                "discovery: ignoring non-UTF-8 datagram from %s:%d", *addr
            )
            return
        try:
            unit_id, unit_port, tcp_port, token = parse_discovery(message)
        except ValueError as error:
            logger.debug(
                "discovery: ignoring datagram from %s:%d: %s",
                addr[0],
                addr[1],
                error,
            )
            return
        if token != self._config.trusted_token:
            logger.warning(
                "discovery: ignoring announcement of unit %r from %s:%d: "
                "mismatched trusted_token",
                unit_id,
                addr[0],
                addr[1],
            )
            return
        if unit_id == self._unit.identifier:
            logger.debug(
                "discovery: ignoring the announcement of unit %r from %s:%s, "
                "it is this unit itself (announcement coming back through the "
                "broadcast)",
                unit_id,
                addr[0],
                addr[1],
            )
            # Our own announcement came back from this address, so it is an
            # interface of this machine, even when the hostname of the
            # machine does not resolve to it.
            if (
                addr[0] not in self._local_addresses
                and addr[0] not in self._learned_addresses
            ):
                self._learned_addresses.add(addr[0])
                logger.debug(
                    "discovery: learned %s as a local interface address",
                    addr[0],
                )
            return
        logger.debug(
            "discovery: valid announcement of unit %r from %s:%s "
            "(UDP port %s, TCP port %s)",
            unit_id,
            addr[0],
            addr[1],
            unit_port,
            tcp_port,
        )
        self._register_peer(unit_id, unit_port, tcp_port, addr)
        self._forward(data, addr, unit_id)

    def _register_peer(self, unit_id, unit_port, tcp_port, addr):
        """Record one valid announcement and register the peer as remote."""
        address = addr[0]
        if _is_loopback_address(address):
            local, reason = True, "a loopback address"
        elif address in self._local_addresses:
            local, reason = True, "one of the local interface addresses"
        elif address in self._learned_addresses:
            local = True
            reason = "an address this unit's own announcement came back from"
        else:
            local = False
            reason = "not one of the local interface addresses"
        logger.debug(
            "discovery: unit %r announced from %s: %s, so it is a %s unit",
            unit_id,
            address,
            reason,
            "local" if local else "remote",
        )
        is_new = unit_id not in self._peers
        self._peers[unit_id] = {
            "address": address,
            "unit_port": unit_port,
            "tcp_port": tcp_port,
            "local": local,
            "last_seen": time.monotonic(),
        }
        if is_new:
            logger.info(
                "discovery: found unit %r at %s (UDP port %d, TCP port %d, "
                "%s host)",
                unit_id,
                address,
                unit_port,
                tcp_port,
                "local" if local else "remote",
            )
        # For synchronization purposes every unit on the network that is
        # not this one is a remote unit.
        self._unit.register_remote(unit_id)

    def expire_peers(self):
        """Forget the peers silent for at least ``expire_after`` seconds.

        Returns the number of forgotten peers.
        """
        now = time.monotonic()
        expired = [
            unit_id
            for unit_id, peer in self._peers.items()
            if now - peer["last_seen"] >= self._expire_after
        ]
        for unit_id in expired:
            del self._peers[unit_id]
            self._unit.forget_remote(unit_id)
            logger.info(
                "discovery: unit %r silent for over %.0f s; forgotten",
                unit_id,
                self._expire_after,
            )
        return len(expired)

    async def _announce_loop(self):
        while True:
            await asyncio.sleep(self._announce_interval)
            self.expire_peers()
            await self.announce()


# ------------------------------------------------------------ HTTP plumbing


class _ProtocolError(Exception):
    """An HTTP request that cannot be handled; carries the error status."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


async def _read_request(reader):
    """Read one HTTP request from ``reader``.

    Returns ``(method, path, query, headers, body)`` where ``query`` is a
    parsed query dict, ``headers`` maps lower-cased header names to their
    value and ``body`` the raw body bytes.  Returns ``None`` when the
    connection closes before a request arrives.  Raises
    :class:`_ProtocolError` for malformed requests.
    """
    try:
        header_data = await reader.readuntil(b"\r\n\r\n")
    except (asyncio.IncompleteReadError, ConnectionError):
        return None
    lines = header_data.decode("iso-8859-1").split("\r\n")
    parts = lines[0].split(" ")
    if len(parts) != 3 or not parts[2].startswith("HTTP/1."):
        raise _ProtocolError(400, "bad request line")
    method, target = parts[0], parts[1]
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()
    body = b""
    if "content-length" in headers:
        try:
            length = int(headers["content-length"])
        except ValueError:
            raise _ProtocolError(400, "invalid Content-Length header")
        if length < 0 or length > _MAX_BODY:
            raise _ProtocolError(413, "payload too large")
        body = await reader.readexactly(length) if length else b""
    query = parse_qs(urlsplit(target).query)
    return method, urlsplit(target).path, query, headers, body


async def _write_response(writer, status, content_type, payload):
    """Write one HTTP/1.1 response (``Connection: close``)."""
    body = payload.encode("utf-8") if isinstance(payload, str) else payload
    lines = [f"HTTP/1.1 {status} {_STATUS_TEXTS.get(status, 'Error')}"]
    if status == 303:
        lines.append("Location: /summary")
    if content_type is not None:
        lines.append(f"Content-Type: {content_type}")
    lines.append(f"Content-Length: {len(body)}")
    lines.append("Connection: close")
    writer.write("\r\n".join(lines).encode("ascii") + b"\r\n\r\n" + body)
    await writer.drain()


async def _handle_connection(unit, reader, writer):
    """Serve one client connection (one request, then close).

    A ``file`` request is answered here directly: its body is streamed from
    the repository instead of being buffered into a response tuple, so the
    other endpoints go through :meth:`Unit.handle_http` first.
    """
    try:
        request = await _read_request(reader)
        if request is None:
            return
        method, path, query, headers, body = request
        if path == "/file":
            await unit.serve_file(writer, method, query, headers)
            return
        status, content_type, payload = await unit.handle_http(
            method, path, query, body
        )
        await _write_response(writer, status, content_type, payload)
    except _ProtocolError as error:
        try:
            await _write_response(
                writer,
                error.status,
                "text/plain; charset=utf-8",
                error.message + "\n",
            )
        except (ConnectionError, OSError):
            pass
    except (asyncio.IncompleteReadError, ConnectionError, OSError):
        pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except (ConnectionError, OSError):
            pass


# --------------------------------------------------------------- file serving


def _check_served_file(full_path, file_hash, modified, max_size):
    """Verify a tracked file before its content leaves this unit.

    ``file_hash`` and ``modified`` are what the manifest records for the
    path.  Returns a ``(size, refusal)`` pair: ``size`` is the number of
    bytes to announce when the file may be served, ``refusal`` is a
    ``(status code, one line message)`` pair taken from the table of the
    "File request" section of ``docs/software_architecture.md`` and ``None``
    when the file may be served.

    Answering with a status code instead of rescanning the repository keeps
    the integrity check the only code that writes a manifest; a mismatch
    here always means the local manifest is stale and the requester has to
    start again from a fresh ``status`` request.
    """
    try:
        # lstat, not stat: a tracked path that became a symlink is not a regular
        # file, and following it would serve whatever it points to - possibly
        # outside the repository.  The manifest never tracks a symlink either.
        info = full_path.lstat()
    except OSError as error:
        return None, (409, f"{full_path} disappeared from the repository: {error}")
    if not stat.S_ISREG(info.st_mode):
        return None, (415, f"{full_path} is not a regular file")
    if int(info.st_mtime) != int(modified.timestamp()):
        return None, (
            409,
            f"{full_path} was modified since the last integrity check",
        )
    if max_size is not None and info.st_size > max_size:
        return None, (
            413,
            f"{full_path} is {info.st_size} bytes, above "
            f"max_transfer_size={max_size}",
        )
    try:
        digest = hash_file(full_path)
    except OSError as error:
        return None, (409, f"{full_path} cannot be read: {error}")
    if digest != file_hash:
        return None, (
            409,
            f"{full_path} does not match the hash {file_hash} its manifest "
            f"records (read {digest})",
        )
    return info.st_size, None


async def _serve_head(writer, status, content_type=None, extra_headers=None):
    """Write one HTTP/1.1 head whose body the caller streams afterwards."""
    lines = [f"HTTP/1.1 {status} {_STATUS_TEXTS.get(status, 'Error')}"]
    if content_type is not None:
        lines.append(f"Content-Type: {content_type}")
    for name, value in (extra_headers or {}).items():
        lines.append(f"{name}: {value}")
    lines.append("Connection: close")
    writer.write("\r\n".join(lines).encode("iso-8859-1") + b"\r\n\r\n")
    await writer.drain()


async def _write_text_response(writer, status, message):
    """Refuse a request with the one line ``text/plain`` body of the protocol."""
    await _write_response(writer, status, "text/plain; charset=utf-8", message + "\n")


async def _stream_file(writer, full_path, chunk_size=_CHUNK_SIZE):
    """Send the content of ``full_path`` in chunks; return the byte count.

    Each chunk is read in a worker thread and written into the transport,
    whose flow control pauses the loop while the peer is not reading: a
    large file costs one chunk of memory and never blocks the unit.  No
    timeout applies here; the head of the answer already announced the size,
    so a peer that disappears simply stops reading and the exception that
    follows ends the transfer.
    """

    def _read_chunk(handle):
        return handle.read(chunk_size)

    sent = 0
    with open(full_path, "rb") as handle:
        while True:
            chunk = await asyncio.to_thread(_read_chunk, handle)
            if not chunk:
                break
            writer.write(chunk)
            await writer.drain()
            sent += len(chunk)
    return sent


# ------------------------------------------------------------ client plumbing


#: Largest response head accepted from a peer unit.
_MAX_HEAD = 64 * 1024


def _request_head(target, token=None):
    """The bytes of one ``GET`` request head sent to a peer unit.

    ``target`` is the path plus query string.  Only a ``file`` request is
    given the shared ``token``: the other endpoints expose status information
    alone (see the "File request" section of
    ``docs/software_architecture.md``).
    """
    lines = [f"GET {target} HTTP/1.1", "Connection: close"]
    if token is not None:
        lines.append(f"{FILE_TOKEN_HEADER}: {token}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("iso-8859-1")


async def _read_response_head(reader):
    """Read one HTTP response head from a peer.

    Returns a ``(status code, reason, headers)`` tuple, with lower-cased
    header names.  Raises ``ValueError`` for anything that is not a
    well-formed head, which is how a requester notices that its peer does not
    speak this version of the protocol.  A peer that closes the connection
    before answering, or that sends a head too large to read, is reported the
    same way: the callers of this helper all know what to do with a
    ``ValueError`` and nothing else.
    """
    try:
        data = await reader.readuntil(b"\r\n\r\n")
    except asyncio.IncompleteReadError:
        raise ValueError("the peer closed the connection before answering") from None
    except asyncio.LimitOverrunError:
        raise ValueError(
            f"no response head was found within the {_MAX_HEAD} bytes allowed"
        ) from None
    if len(data) > _MAX_HEAD:
        raise ValueError(f"a response head above {_MAX_HEAD} bytes was sent")
    lines = data.decode("iso-8859-1").split("\r\n")
    parts = lines[0].split(" ", 2)
    if len(parts) < 2 or not parts[0].startswith("HTTP/1."):
        raise ValueError(f"not an HTTP response status line: {lines[0]!r}")
    try:
        status = int(parts[1])
    except ValueError:
        raise ValueError(f"malformed HTTP status code: {parts[1]!r}") from None
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            name, value = line.split(":", 1)
            headers[name.strip().lower()] = value.strip()
    return status, (parts[2] if len(parts) > 2 else ""), headers


async def _read_body(reader, head, timeout):
    """Read a whole response body: the announced ``Content-Length``, else EOF.

    Raises ``ValueError`` for a truncated answer, so a caller never works
    with a half received message.
    """
    if "content-length" not in head:
        return await _read_until_eof(reader, timeout)
    try:
        size = int(head["content-length"])
    except ValueError:
        raise ValueError(
            f"malformed Content-Length header: {head['content-length']!r}"
        ) from None
    if size <= 0:
        return b""
    try:
        return await asyncio.wait_for(reader.readexactly(size), timeout)
    except asyncio.IncompleteReadError as error:
        raise ValueError(
            f"the answer ended after {len(error.partial)} of the {size} "
            "announced bytes"
        ) from error


async def _read_until_eof(reader, timeout):
    """Read a response body that announced no length, until the peer closes."""
    chunks = []
    while True:
        chunk = await asyncio.wait_for(reader.read(_CHUNK_SIZE), timeout)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


async def _stream_into_file(reader, part_path, size, timeout, max_size=None):
    """Stream ``size`` bytes of a response body into ``part_path``.

    Returns the ``(bytes written, md5 digest)`` pair the caller compares with
    what the head of the answer announced: the content is written as it
    arrives and never buffered.  A body that grows past ``max_size`` is given
    up on immediately -- that is the receiving side of ``max_transfer_size``:
    a peer that ignores its own limit must not fill up the repository.

    Raises ``ValueError`` for a body above ``max_size`` and ``TimeoutError``
    when the peer stays idle for ``timeout`` seconds.
    """

    digest = hashlib.md5()
    written = 0
    with open(part_path, "wb") as handle:
        while written < size:
            chunk = await asyncio.wait_for(
                reader.read(min(_CHUNK_SIZE, size - written)), timeout
            )
            if not chunk:
                break  # the peer disappeared mid-transfer
            written += len(chunk)
            if max_size is not None and written > max_size:
                raise ValueError(
                    f"the peer sent more than the {max_size} bytes of "
                    "max_transfer_size"
                )
            digest.update(chunk)
            handle.write(chunk)
    return written, digest


def _publish_fetched_file(config, part_path, full_path):
    """Give a verified temporary file its configured properties and place it.

    The access mode and the ownership a unit is configured to give the files
    it fetches (``fetched_file_mode``, ``fetched_owner_id`` and
    ``fetched_group_id``) are applied while the file is still out of sight;
    the rename onto the target is atomic within a filesystem, so a file that
    is visible in a repository is always a complete version.  When they
    cannot be applied -- a missing privilege, for example -- the temporary
    file is deleted and the fetch fails rather than publishing a file without
    its configured properties.
    """
    try:
        if config.fetched_file_mode is not None:
            os.chmod(part_path, config.fetched_file_mode)
        if config.fetched_owner_id is not None or config.fetched_group_id is not None:
            os.chown(
                part_path,
                -1 if config.fetched_owner_id is None else config.fetched_owner_id,
                -1 if config.fetched_group_id is None else config.fetched_group_id,
            )
        os.replace(part_path, full_path)
    except OSError as error:
        part_path.unlink(missing_ok=True)
        raise TransferError(
            f"cannot place the fetched file {full_path}: {error}"
        ) from error


# ---------------------------------------------------------------------- run


def _describe(delay, schedule):
    """Human readable description of a delay/schedule pair."""
    if delay is not None:
        return f"every {delay} seconds"
    return f"cron {schedule!r}"


async def run(config):
    """Run the unit described by ``config`` until SIGINT/SIGTERM."""
    unit = Unit(config)
    unit.load_manifests()
    logger.info(
        "unit %r starting: %d repository(ies): %s",
        config.identifier,
        len(config.repository),
        ", ".join(repo.name for repo in config.repository),
    )
    logger.info(
        "integrity check: %s",
        _describe(config.integrity_delay, config.integrity_schedule),
    )
    logger.info(
        "synchronization: %s",
        _describe(config.sync_delay, config.sync_schedule),
    )

    address = config.address or "0.0.0.0"
    discovery = None
    try:
        if config.discovery_port is not None:
            discovery = Discovery(unit)
            await discovery.start()
            # synchronization asks this table where a unit is at the moment
            # its files are fetched.
            unit.set_discovery(discovery)
        else:
            logger.info(
                "network discovery: disabled (no discovery_port configured)"
            )

        server = await asyncio.start_server(
            lambda reader, writer: _handle_connection(unit, reader, writer),
            address,
            config.port,
        )
        logger.info("listening on %s:%d", address, config.port)

        def unit_is_busy():
            """Whether an integrity check or a synchronization round runs."""
            return unit.is_busy

        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except (NotImplementedError, RuntimeError, ValueError):
                pass

        tasks = [
            asyncio.create_task(
                schedule_loop(
                    unit.integrity_check,
                    config.integrity_delay,
                    config.integrity_schedule,
                    busy_check=unit_is_busy,
                ),
                name="integrity-check",
            ),
            asyncio.create_task(
                schedule_loop(
                    unit.sync,
                    config.sync_delay,
                    config.sync_schedule,
                    busy_check=unit_is_busy,
                ),
                name="synchronization",
            ),
        ]
        try:
            await stop.wait()
            logger.info("shutting down")
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            server.close()
            await server.wait_closed()
    finally:
        if discovery is not None:
            await discovery.stop()


#: Log levels accepted by the ``--log-level`` command line option.
_LOG_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}


def setup_logging(level=logging.INFO):
    """Log to stdout at ``level`` (see the ``--log-level`` option).

    The level is set on the ``beacon`` logger as well, not only on the
    root logger: :func:`logging.basicConfig` does nothing when logging
    was already configured (tests, an embedding script), and an explicit
    level keeps the requested verbosity in that case too.  ``debug`` is
    what surfaces the chatty discovery details (ignored datagrams,
    forwarding failures, UDP errors).
    """
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(message)s",
        stream=sys.stdout,
    )
    logging.getLogger("beacon").setLevel(level)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run a Beacon backup unit.")
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="unit config file to load (default: config.yaml)",
    )
    parser.add_argument(
        "--log-level",
        default="info",
        choices=tuple(_LOG_LEVELS),
        type=str.lower,
        metavar="LEVEL",
        help="log verbosity: %s (default: %%(default)s)"
        % ", ".join(_LOG_LEVELS),
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="shortcut for --log-level debug",
    )
    args = parser.parse_args(argv)
    level = _LOG_LEVELS["debug" if args.verbose else args.log_level]
    try:
        config = UnitConfig.load(args.config)
    except (OSError, ValueError, yaml.YAMLError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    setup_logging(level)
    try:
        asyncio.run(run(config))
    except KeyboardInterrupt:
        logger.info("interrupted")
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

