#!/usr/bin/env python3
"""Beacon unit core script (single-unit configuration).

``unit.py`` is the core utility of the backup network (see the "Core
tools" section of the project readme).  This is the single-unit
configuration: the unit tracks its repositories and answers status
requests, but does not discover other units nor synchronize files with
them (peer discovery and cross-unit synchronization are added later;
see ``docs/synchronization.md``).

On start up the script:

1. reads and validates the YAML config file (``unit_config.UnitConfig``);
2. loads the local manifest for every configured repository (an empty
   manifest is created when the file does not exist yet);
3. starts an HTTP server on the configured address and port that answers
   (message formats are defined by ``network_messages``):
   - ``GET /status`` -- JSON short status plus base64-encoded manifests
     (unit x unit);
   - ``GET /network`` -- JSON list of all units in the network (this unit
     plus any known units; unit x desktop_notify);
   - ``GET /summary`` -- the user facing HTML status page; ``GET /``
     redirects here;
   - ``POST /reevaluate`` -- re-evaluates the integrity issues of one
     repository (``repository=NAME`` given as a query parameter or a form
     field) and clears the faults that are no longer reproducible;
4. spawns two async tasks that run once immediately and then on the
   configured schedule:
   - the integrity check re-scans every repository through
     ``Manifest.update_from_directory`` (new files, changed hashes,
     deletions, I/O errors and hash changes without a modification time
     change all update the manifest and its issue list), saves it under
     ``<manifest_storage>/<repository name>.manifest`` and logs the
     result;
   - the synchronization is a no-op: no peers are known in the
     single-unit configuration;
5. runs until it receives SIGINT or SIGTERM, then shuts down cleanly.

Usage:
    unit.py [--config FILE]
"""

import argparse
import asyncio
import logging
import os
import signal
import stat
import sys
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import yaml

from manifest import Manifest, current_hash, hash_file, make_hash_entry
from network_messages import (
    compute_status,
    generate_network_status,
    generate_status,
    generate_summary,
    manifest_status,
)
from unit_config import UnitConfig, validate_schedule

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
    404: "Not Found",
    405: "Method Not Allowed",
    413: "Payload Too Large",
    500: "Internal Server Error",
}


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


async def schedule_loop(action, delay=None, schedule=None):
    """Run ``action`` once, then repeat according to the schedule.

    ``action`` is an async callable.  Exactly one of ``delay`` (seconds
    between runs) or ``schedule`` (5-field cron string) must be given;
    ``UnitConfig`` guarantees this for the unit's tasks.
    """
    if (delay is None) == (schedule is None):
        raise ValueError("exactly one of delay or schedule must be set")
    while True:
        await action()
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
    repository, the start time and the list of known remotes (always empty
    in the single-unit configuration).  All mutations happen under
    ``Unit.lock`` so the HTTP handlers always see a consistent snapshot.
    """

    def __init__(self, config):
        self._config = config
        self._started = datetime.now()
        self._manifests = {}
        # name -> {"status": short status, "last_sync": datetime | None}
        self._remotes = {}
        self._lock = asyncio.Lock()

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

        Always empty in the single-unit configuration.
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
        repositories are still checked.
        """
        async with self._lock:
            for name in self.known_repositories():
                try:
                    status = await asyncio.to_thread(
                        self._check_repository, name
                    )
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

    # ---------------------------------------------------------- sync (stub)

    async def sync(self):
        """Synchronize the repositories with peer units.

        Single-unit configuration: no peers are known, so nothing is
        transferred.  The hook keeps the synchronization schedule alive
        for the future multi-unit implementation.
        """
        async with self._lock:
            repositories = self.known_repositories()
        logger.info(
            "synchronization: no known peers; %d repository(ies) unchanged",
            len(repositories),
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


# ------------------------------------------------------------ HTTP plumbing


class _ProtocolError(Exception):
    """An HTTP request that cannot be handled; carries the error status."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


async def _read_request(reader):
    """Read one HTTP request from ``reader``.

    Returns ``(method, path, query, body)`` where ``query`` is a parsed
    query dict and ``body`` the raw body bytes.  Returns ``None`` when the
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
    return method, urlsplit(target).path, query, body


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
    """Serve one client connection (one request, then close)."""
    try:
        request = await _read_request(reader)
        if request is None:
            return
        method, path, query, body = request
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
    server = await asyncio.start_server(
        lambda reader, writer: _handle_connection(unit, reader, writer),
        address,
        config.port,
    )
    logger.info("listening on %s:%d", address, config.port)

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
            ),
            name="integrity-check",
        ),
        asyncio.create_task(
            schedule_loop(unit.sync, config.sync_delay, config.sync_schedule),
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


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run a Beacon backup unit.")
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="unit config file to load (default: config.yaml)",
    )
    args = parser.parse_args(argv)
    try:
        config = UnitConfig.load(args.config)
    except (OSError, ValueError, yaml.YAMLError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
        stream=sys.stdout,
    )
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

