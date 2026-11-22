#!/usr/bin/env python3
"""Beacon network message formatting module.

This module is the single point that defines the format of the network
messages exchanged by Beacon units (see the "Network communications"
section of ``docs/software_architecture.md``).  It only formats outgoing
messages and parses incoming ones; it contains no socket or HTTP code.

Message formats
---------------
``/status`` (unit x unit) -- produced by :func:`generate_status` and
decoded by :func:`parse_status`.  A JSON object with two keys::

    {
        "status": "OK" | "faulty" | "conflicts",
        "manifests": [<base64-encoded manifest>, ...]
    }

Each base64-encoded manifest is a compact JSON object::

    {
        "version": 1,
        "name": "<repository name>",
        "path": "<local repository root>",
        "unit_id": "<unit name> or null",
        "files": {
            "<relative path>": {
                "history": ["hash:<md5>", ...],
                "timestamp": "<ISO 8601 datetime>"
            }
        },
        "issues": [
            {"type": "fault" | "conflict", "path": "<relative path>",
             "remote": "<unit name> or null", "description": "<text>"}
        ]
    }

``/network`` (unit x desktop_notify) -- produced by
:func:`generate_network_status` and decoded by
:func:`parse_network_status`.  A JSON list with one object per known
unit::

    [{"name": ..., "status": ..., "last_sync": "<ISO 8601>" | null}]

``discover`` (unit x unit, UDP announcement, see the "Network discovery
protocol" section of ``docs/software_architecture.md``) -- produced by
:func:`generate_discovery` and decoded by :func:`parse_discovery`.  A
compact JSON object::

    {
        "unit_id": "<unit name>",
        "unit_port": <UDP port the announcing unit listens on>,
        "tcp_port": <announcing unit's TCP port>,
        "trusted_token": "<shared network token>"
    }

``/file`` (unit x unit) -- the request query is decoded by
:func:`parse_file_target` (whose ``path`` field is validated by
:func:`canonical_rel_path`), and the headers of a successful answer are
built by :func:`generate_file_headers` and decoded by
:func:`parse_file_headers`.  The answer carries the raw file bytes and
three headers::

    Content-Length: <size in bytes>
    X-Beacon-File-Hash: <md5 hex digest the serving unit's manifest records>
    X-Beacon-Unit: <serving unit name>

The request carries the shared token in the ``X-Beacon-Token`` header
(:data:`FILE_TOKEN_HEADER`); it is the only request that exposes user data
and therefore the only one that is authenticated.  No file system metadata
travels with the body: the receiving unit decides mode, owner and group of
the file it writes (see the "File request" section of
``docs/software_architecture.md``).

``/summary`` (unit x user) -- :func:`generate_summary` renders the HTML
page that answers a GET to ``<unit_address>/summary``.

Short statuses
--------------
Every unit and every repository carries one of three short statuses:

- ``STATUS_OK``: no unresolved issues;
- ``STATUS_FAULTY``: at least one ``fault`` issue;
- ``STATUS_CONFLICTS``: no fault issues, but at least one ``conflict``
  issue.

``STATUS_FAULTY`` takes precedence over ``STATUS_CONFLICTS``: a faulty
repository waits for a human, so a repository that has both issue types
reports ``faulty`` (see the "faulty marker" in the readme and
``Manifest._register_conflict``).

All parse functions raise ``ValueError`` for messages that do not match
the expected format.
"""

import base64
import html
import json
import re
from datetime import datetime
from pathlib import Path

from manifest import ISSUE_TYPES, MANIFEST_VERSION, Manifest

# ------------------------------------------------------------------ statuses

#: Short statuses used in network messages and on the summary page.
STATUS_OK = "OK"
STATUS_FAULTY = "faulty"
STATUS_CONFLICTS = "conflicts"
STATUS_VALUES = (STATUS_OK, STATUS_FAULTY, STATUS_CONFLICTS)

#: Higher value means a more severe status.
_STATUS_SEVERITY = {
    STATUS_OK: 0,
    STATUS_CONFLICTS: 1,
    STATUS_FAULTY: 2,
}

_REEVALUATE_ACTION = "/reevaluate"


def _type_name(value):
    return type(value).__name__


def manifest_status(manifest):
    """Short status of one repository :class:`Manifest`.

    Returns one of ``STATUS_OK``, ``STATUS_FAULTY`` or
    ``STATUS_CONFLICTS``.  A manifest that has both fault and conflict
    issues is ``faulty``.
    """
    if manifest.is_faulty():
        return STATUS_FAULTY
    if any(issue["type"] == "conflict" for issue in manifest.issues):
        return STATUS_CONFLICTS
    return STATUS_OK


def compute_status(manifests):
    """Overall short status of a unit from its repository manifests.

    The result is the most severe repository status: ``faulty`` beats
    ``conflicts``, which beats ``OK``.  An empty list yields
    ``STATUS_OK``.
    """
    status = STATUS_OK
    for manifest in manifests:
        repo_status = manifest_status(manifest)
        if _STATUS_SEVERITY[repo_status] > _STATUS_SEVERITY[status]:
            status = repo_status
    return status


def format_uptime(total_seconds):
    """Format a duration in seconds as a human readable string.

    ``0`` becomes ``"0 seconds"``, ``3661`` becomes ``"1 hour, 1 minute,
    1 second"``.  Only non-zero units are shown, largest first, with
    pluralization.  Raises ``ValueError`` for non-integers or negative
    values.
    """
    if isinstance(total_seconds, bool) or not isinstance(total_seconds, int):
        raise ValueError(
            f"uptime must be an integer number of seconds, "
            f"got {_type_name(total_seconds)}"
        )
    if total_seconds < 0:
        raise ValueError(
            f"uptime must not be negative, got {total_seconds}"
        )
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts = []
    for value, unit in (
        (days, "day"),
        (hours, "hour"),
        (minutes, "minute"),
        (seconds, "second"),
    ):
        if value:
            parts.append(f"{value} {unit}" + ("" if value == 1 else "s"))
    return ", ".join(parts) if parts else "0 seconds"


# ------------------------------------------------------------ manifest codec


def _escape(value):
    """HTML-escape a value for use in text or attribute content."""
    return html.escape(str(value), quote=True)


def _manifest_to_dict(manifest):
    """Serialize a :class:`Manifest` to a JSON-compatible dict."""
    files = {}
    for rel_path, metadata in manifest.files.items():
        files[rel_path] = {
            "history": list(metadata["history"]),
            "timestamp": metadata["timestamp"].isoformat(),
        }
    issues = [
        {
            "type": issue["type"],
            "path": Path(issue["path"]).as_posix(),
            "remote": issue["remote"],
            "description": issue["description"],
        }
        for issue in manifest.issues
    ]
    return {
        "version": manifest.version,
        "name": manifest.name,
        "path": Path(manifest.path).as_posix(),
        "unit_id": manifest.unit_id,
        "files": files,
        "issues": issues,
    }


def _manifest_from_dict(data):
    """Build and validate a :class:`Manifest` from a JSON-compatible dict."""
    if not isinstance(data, dict):
        raise ValueError(
            f"manifest payload must be a JSON object, got {_type_name(data)}"
        )
    version = data.get("version")
    if version != MANIFEST_VERSION:
        raise ValueError(
            f"unsupported manifest version {version!r} "
            f"(expected {MANIFEST_VERSION})"
        )
    name = data.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError("manifest name must be a non-empty string")
    path = data.get("path")
    if not isinstance(path, str) or not path:
        raise ValueError("manifest path must be a non-empty string")
    files_data = data.get("files")
    if not isinstance(files_data, dict):
        raise ValueError("manifest files must be a JSON object")
    issues_data = data.get("issues")
    if not isinstance(issues_data, list):
        raise ValueError("manifest issues must be a JSON list")
    unit_id = data.get("unit_id")
    if unit_id is not None and not isinstance(unit_id, str):
        raise ValueError("manifest unit_id must be a string or null")

    manifest = Manifest(name, path, unit_id=unit_id)
    for rel_path, metadata in files_data.items():
        if not isinstance(metadata, dict):
            raise ValueError(
                f"file metadata for {rel_path!r} must be a JSON object"
            )
        history = metadata.get("history")
        if not isinstance(history, list) or not all(
            isinstance(entry, str) for entry in history
        ):
            raise ValueError(
                f"history for {rel_path!r} must be a list of strings"
            )
        timestamp = metadata.get("timestamp")
        try:
            parsed = datetime.fromisoformat(timestamp)
        except (TypeError, ValueError):
            raise ValueError(
                f"timestamp for {rel_path!r} is not a valid "
                f"ISO 8601 datetime: {timestamp!r}"
            )
        manifest.files[rel_path] = {
            "history": list(history),
            "timestamp": parsed,
        }
    for issue in issues_data:
        if not isinstance(issue, dict):
            raise ValueError("issue entries must be JSON objects")
        issue_type = issue.get("type")
        if issue_type not in ISSUE_TYPES:
            raise ValueError(
                f"unknown issue type {issue_type!r} "
                f"(expected one of {', '.join(ISSUE_TYPES)})"
            )
        issue_path = issue.get("path")
        if not isinstance(issue_path, str) or not issue_path:
            raise ValueError("issue path must be a non-empty string")
        remote = issue.get("remote")
        if remote is not None and not isinstance(remote, str):
            raise ValueError("issue remote must be a string or null")
        description = issue.get("description")
        if not isinstance(description, str):
            raise ValueError("issue description must be a string")
        manifest.issues.append(
            {
                "type": issue_type,
                "path": Path(issue_path),
                "remote": remote,
                "description": description,
            }
        )
    return manifest


def encode_manifest(manifest):
    """Base64-encode a :class:`Manifest` for network transfer.

    The payload is the compact JSON object described in the module
    docstring.
    """
    if not isinstance(manifest, Manifest):
        raise ValueError(
            f"manifest must be a Manifest object, got {_type_name(manifest)}"
        )
    payload = json.dumps(
        _manifest_to_dict(manifest), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return base64.b64encode(payload).decode("ascii")


def decode_manifest(encoded):
    """Decode a string produced by :func:`encode_manifest`.

    Returns a :class:`Manifest`.  Raises ``ValueError`` if the string is
    not valid base64 JSON or does not match the expected manifest layout.
    """
    if not isinstance(encoded, str):
        raise ValueError(
            f"encoded manifest must be a string, got {_type_name(encoded)}"
        )
    try:
        raw = base64.b64decode(encoded, validate=True)
    except ValueError:
        raise ValueError("encoded manifest is not valid base64")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ValueError("encoded manifest is not valid JSON")
    return _manifest_from_dict(data)


# ------------------------------------------------------------ status message


def generate_status(manifests):
    """Encode the answer for the ``/status`` endpoint as a JSON string.

    ``manifests`` is a list of :class:`Manifest` objects (one per
    repository).  The result is a JSON object with the unit's short
    status and one base64-encoded manifest per repository (see the
    module docstring for the exact layout).
    """
    manifest_list = list(manifests)
    for manifest in manifest_list:
        if not isinstance(manifest, Manifest):
            raise ValueError(
                f"manifests must be a list of Manifest objects, "
                f"got {_type_name(manifest)}"
            )
    payload = {
        "status": compute_status(manifest_list),
        "manifests": [encode_manifest(m) for m in manifest_list],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def parse_status(message):
    """Decode a ``/status`` answer produced by :func:`generate_status`.

    Returns a ``(status, manifests)`` tuple, where ``status`` is the
    short status reported by the sending unit (one of ``STATUS_OK``,
    ``STATUS_FAULTY``, ``STATUS_CONFLICTS``) and ``manifests`` is a list
    of :class:`Manifest` objects.  Raises ``ValueError`` for messages
    that do not match the expected format.
    """
    if not isinstance(message, str):
        raise ValueError(
            f"status message must be a string, got {_type_name(message)}"
        )
    try:
        data = json.loads(message)
    except ValueError:
        raise ValueError("status message is not valid JSON")
    if not isinstance(data, dict):
        raise ValueError(
            f"status message must be a JSON object, got {_type_name(data)}"
        )
    status = data.get("status")
    if status not in STATUS_VALUES:
        raise ValueError(
            f"unknown status {status!r} (expected one of "
            f"{', '.join(STATUS_VALUES)})"
        )
    encoded_list = data.get("manifests")
    if not isinstance(encoded_list, list):
        raise ValueError("status message is missing the 'manifests' list")
    return status, [decode_manifest(encoded) for encoded in encoded_list]



# ---------------------------------------------------------- network message


def _validate_remotes(remotes):
    """Validate a list of ``(name, status, last sync time)`` tuples."""
    remotes = list(remotes)
    for remote in remotes:
        if not isinstance(remote, (tuple, list)) or len(remote) != 3:
            raise ValueError(
                "remotes must be an iterable of "
                "(name, status, last sync time) tuples, got "
                f"{remote!r}"
            )
        name, status, last_sync = remote
        if not isinstance(name, str) or not name.strip():
            raise ValueError(
                f"remote name must be a non-empty string, got {name!r}"
            )
        if status not in STATUS_VALUES:
            raise ValueError(
                f"remote {name!r} has unknown status {status!r} "
                f"(expected one of {', '.join(STATUS_VALUES)})"
            )
        if last_sync is not None and not isinstance(last_sync, datetime):
            raise ValueError(
                f"last sync time for remote {name!r} must be a datetime "
                f"or None, got {_type_name(last_sync)}"
            )
    return remotes


def generate_network_status(remotes):
    """Encode the answer for the ``/network`` endpoint as a JSON string.

    ``remotes`` is a list of ``(name, status, last sync time)`` tuples,
    where the status is one of ``STATUS_OK``, ``STATUS_FAULTY``,
    ``STATUS_CONFLICTS`` and the last sync time is a
    :class:`datetime` or ``None``.  The result is a JSON list with one
    ``{"name": ..., "status": ..., "last_sync": ...}`` object per
    remote, the last sync time serialized as an ISO 8601 string.
    """
    remotes = _validate_remotes(remotes)
    payload = [
        {
            "name": name,
            "status": status,
            "last_sync": last_sync.isoformat()
            if last_sync is not None
            else None,
        }
        for name, status, last_sync in remotes
    ]
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def parse_network_status(message):
    """Decode a ``/network`` answer produced by
    :func:`generate_network_status`.

    Returns a list of ``(name, status, last sync time)`` tuples, with
    the last sync time as a :class:`datetime` (or ``None``).  Raises
    ``ValueError`` for messages that do not match the expected format.
    """
    if not isinstance(message, str):
        raise ValueError(
            f"network status message must be a string, "
            f"got {_type_name(message)}"
        )
    try:
        data = json.loads(message)
    except ValueError:
        raise ValueError("network status message is not valid JSON")
    if not isinstance(data, list):
        raise ValueError(
            f"network status message must be a JSON list, "
            f"got {_type_name(data)}"
        )
    remotes = []
    for entry in data:
        if not isinstance(entry, dict):
            raise ValueError(
                f"network status entries must be JSON objects, "
                f"got {_type_name(entry)}"
            )
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(
                f"network status entry is missing a valid name: {entry!r}"
            )
        status = entry.get("status")
        if status not in STATUS_VALUES:
            raise ValueError(
                f"unknown status {status!r} in network status entry "
                f"for {name!r} (expected one of {', '.join(STATUS_VALUES)})"
            )
        last_sync = entry.get("last_sync")
        if last_sync is None:
            parsed_sync = None
        elif not isinstance(last_sync, str):
            raise ValueError(
                f"last sync time for {name!r} must be an ISO 8601 string "
                f"or null, got {_type_name(last_sync)}"
            )
        else:
            try:
                parsed_sync = datetime.fromisoformat(last_sync)
            except ValueError:
                raise ValueError(
                    f"last sync time for {name!r} is not a valid "
                    f"ISO 8601 datetime: {last_sync!r}"
                )
        remotes.append((name, status, parsed_sync))
    return remotes


# ------------------------------------------------------------- discovery message


def _validate_port(field, value):
    """Validate a network port from a discovery message; 1-65535."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(
            f"{field} must be an integer, got {_type_name(value)}"
        )
    if not 1 <= value <= 65535:
        raise ValueError(f"{field} must be in range 1-65535, got {value}")
    return value


def generate_discovery(unit_identifier, unit_port, tcp_port, trusted_token):
    """Encode a UDP network discovery announcement as a JSON string.

    ``unit_identifier`` is the announcing unit's name, ``unit_port`` the
    UDP port the unit listens on for discovery, ``tcp_port`` the unit's
    TCP (HTTP) port and ``trusted_token`` the shared network token; the
    receiver only trusts the announcement when the token equals its own
    (see the "Network discovery protocol" section of
    ``docs/software_architecture.md``).
    """
    if not isinstance(unit_identifier, str) or not unit_identifier.strip():
        raise ValueError(
            f"unit identifier must be a non-empty string, "
            f"got {unit_identifier!r}"
        )
    unit_port = _validate_port("unit_port", unit_port)
    tcp_port = _validate_port("tcp_port", tcp_port)
    if not isinstance(trusted_token, str):
        raise ValueError(
            f"trusted token must be a string, got {_type_name(trusted_token)}"
        )
    payload = {
        "unit_id": unit_identifier,
        "unit_port": unit_port,
        "tcp_port": tcp_port,
        "trusted_token": trusted_token,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def parse_discovery(message):
    """Decode a discovery announcement produced by :func:`generate_discovery`.

    Returns a ``(unit identifier, unit UDP port, unit TCP port, trusted
    token)`` tuple.  The trusted token is returned verbatim; comparing
    it with the local token is the caller's decision.  Raises
    ``ValueError`` for messages that do not match the expected format.
    """
    if not isinstance(message, str):
        raise ValueError(
            f"discovery message must be a string, got {_type_name(message)}"
        )
    try:
        data = json.loads(message)
    except ValueError:
        raise ValueError("discovery message is not valid JSON")
    if not isinstance(data, dict):
        raise ValueError(
            f"discovery message must be a JSON object, "
            f"got {_type_name(data)}"
        )
    unit_id = data.get("unit_id")
    if not isinstance(unit_id, str) or not unit_id:
        raise ValueError(
            f"discovery message is missing a valid unit id: {data!r}"
        )
    for field in ("unit_port", "tcp_port", "trusted_token"):
        if field not in data:
            raise ValueError(
                f"discovery message is missing the {field!r} field: {data!r}"
            )
    unit_port = _validate_port("discovery unit port", data["unit_port"])
    tcp_port = _validate_port("discovery unit TCP port", data["tcp_port"])
    trusted_token = data["trusted_token"]
    if not isinstance(trusted_token, str):
        raise ValueError(
            f"discovery trusted token must be a string, "
            f"got {_type_name(trusted_token)}"
        )
    return unit_id, unit_port, tcp_port, trusted_token


# -------------------------------------------------------------- file message

#: Request header carrying the shared ``trusted_token`` of the backup
#: network; the serving unit refuses a ``file`` request whose token does not
#: match (see the "File request" section of
#: ``docs/software_architecture.md``).
FILE_TOKEN_HEADER = "X-Beacon-Token"

#: Response headers of a successful ``file`` request: the size of the body,
#: the hash the serving unit's manifest records for the file and the name of
#: the serving unit.
CONTENT_LENGTH_HEADER = "Content-Length"
FILE_HASH_HEADER = "X-Beacon-File-Hash"
FILE_UNIT_HEADER = "X-Beacon-Unit"

#: The md5 hex digest ``manifest.py`` stores for a file.
_MD5_RE = re.compile(r"[0-9a-fA-F]{32}")


def canonical_rel_path(path):
    """Validate the ``path`` of a ``file`` request and return its canonical
    form.

    A canonical path is a non-empty relative POSIX path that uses ``/`` as
    its only separator, contains no ``\\``, ``:`` or NUL character and has no
    empty, ``.`` or ``..`` component.  This is what makes a path that arrives
    from a peer safe to resolve under a local repository root; file names
    that need those characters are simply not addressable by a ``file``
    request (they stay tracked in the manifest).  Raises ``ValueError`` for
    anything else.
    """
    if not isinstance(path, str):
        raise ValueError(f"file path must be a string, got {_type_name(path)}")
    if not path:
        raise ValueError("file path must not be empty")
    for character, reason in (
        ("\\", "a backslash: '/' is the only accepted separator"),
        (":", "a colon: absolute and drive-qualified paths are not allowed"),
        ("\x00", "a NUL character"),
    ):
        if character in path:
            raise ValueError(
                f"invalid file path {path!r}: it contains {reason}"
            )
    for component in path.split("/"):
        if not component:
            raise ValueError(
                f"invalid file path {path!r}: it has an empty component "
                "(a leading, trailing or doubled '/')"
            )
        if component in (".", ".."):
            raise ValueError(
                f"invalid file path {path!r}: '.' and '..' components are "
                "not allowed"
            )
    return path


def _single_query_value(query, field):
    """The single value of ``field`` in a parsed request query string.

    Accepts both the ``{name: [value, ...]}`` layout of ``urllib.parse`` and
    a plain ``{name: value}`` mapping.  Raises ``ValueError`` when the field
    is missing or given more than once.
    """
    if not isinstance(query, dict):
        raise ValueError(
            f"file request query must be a mapping, got {_type_name(query)}"
        )
    if field not in query:
        raise ValueError(
            f"file request is missing the {field!r} query parameter"
        )
    value = query[field]
    if isinstance(value, (list, tuple)):
        if len(value) != 1:
            raise ValueError(
                f"file request parameter {field!r} is ambiguous: "
                f"{len(value)} values given"
            )
        value = value[0]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"file request parameter {field!r} must be a non-empty string, "
            f"got {value!r}"
        )
    return value


def parse_file_target(query):
    """Decode the query string of a ``file`` request.

    Returns a ``(repository name, relative path)`` tuple; the path is the
    canonical form validated by :func:`canonical_rel_path`.  Raises
    ``ValueError`` for a missing, ambiguous or non-canonical field.
    """
    repository = _single_query_value(query, "repository")
    rel_path = canonical_rel_path(_single_query_value(query, "path"))
    return repository, rel_path


def _validate_file_hash(field, value):
    """A 32 character md5 hex digest, returned unchanged."""
    if not isinstance(value, str) or _MD5_RE.fullmatch(value) is None:
        raise ValueError(
            f"{field} must be a 32 character hexadecimal md5 digest, "
            f"got {value!r}"
        )
    return value


def _validate_size(field, value):
    """A non-negative byte count, returned unchanged."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(
            f"{field} must be an integer number of bytes, "
            f"got {_type_name(value)}"
        )
    if value < 0:
        raise ValueError(f"{field} must not be negative, got {value}")
    return value


def generate_file_headers(unit_identifier, file_hash, size):
    """Build the headers of a successful ``file`` answer.

    ``unit_identifier`` is the serving unit, ``file_hash`` the md5 hex digest
    its manifest records for the file and ``size`` the number of bytes of the
    body.  The response carries no file system metadata: the receiving unit
    decides the access mode and the ownership of the file it writes.
    """
    if not isinstance(unit_identifier, str) or not unit_identifier.strip():
        raise ValueError(
            f"unit identifier must be a non-empty string, "
            f"got {unit_identifier!r}"
        )
    file_hash = _validate_file_hash("file hash", file_hash)
    size = _validate_size("file size", size)
    return {
        CONTENT_LENGTH_HEADER: str(size),
        FILE_HASH_HEADER: file_hash,
        FILE_UNIT_HEADER: unit_identifier,
    }


def _header_values(headers):
    """Lower-cased header names mapped to their single value."""
    if not isinstance(headers, dict):
        raise ValueError(
            f"file answer headers must be a mapping, got {_type_name(headers)}"
        )
    values = {}
    for name, value in headers.items():
        if not isinstance(name, str):
            raise ValueError(
                "file answer header names must be strings, "
                f"got {_type_name(name)}"
            )
        if isinstance(value, (list, tuple)):
            if len(value) != 1:
                raise ValueError(
                    f"file answer header {name!r} is ambiguous: "
                    f"{len(value)} values given"
                )
            value = value[0]
        values[name.strip().lower()] = value
    return values


def parse_file_headers(headers):
    """Decode the headers of a ``file`` answer.

    Returns a ``(file hash, size, unit identifier)`` tuple; header names are
    matched whatever their casing.  Raises ``ValueError`` for a missing or
    malformed value, which is how a requester notices that a peer does not
    speak this version of the protocol.
    """
    values = _header_values(headers)
    missing = [
        header
        for header in (FILE_HASH_HEADER, CONTENT_LENGTH_HEADER, FILE_UNIT_HEADER)
        if header.lower() not in values
    ]
    if missing:
        raise ValueError(
            "file answer is missing the header(s): " + ", ".join(missing)
        )
    file_hash = _validate_file_hash(
        f"{FILE_HASH_HEADER} header", values[FILE_HASH_HEADER.lower()]
    )
    size = values[CONTENT_LENGTH_HEADER.lower()]
    if isinstance(size, str):
        try:
            size = int(size)
        except ValueError:
            raise ValueError(
                f"{CONTENT_LENGTH_HEADER} header is not a number of bytes: "
                f"{size!r}"
            )
    size = _validate_size(f"{CONTENT_LENGTH_HEADER} header", size)
    unit_identifier = values[FILE_UNIT_HEADER.lower()]
    if not isinstance(unit_identifier, str) or not unit_identifier.strip():
        raise ValueError(
            f"{FILE_UNIT_HEADER} header must be a non-empty string, "
            f"got {unit_identifier!r}"
        )
    return file_hash, size, unit_identifier


# ------------------------------------------------------------- summary page


_SUMMARY_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
</head>
<body>
<h1>Backup unit: {unit_name}</h1>
<p>Uptime: {uptime}</p>
<h2>Repositories</h2>
{repositories}
{conflicts}
<h2>Other units</h2>
{remotes}
</body>
</html>
"""


def _render_repositories(manifests):
    """Repository table; faulty rows carry a /reevaluate form."""
    if not manifests:
        return "<p>This unit does not manage any repositories.</p>"
    rows = []
    for manifest in manifests:
        status = manifest_status(manifest)
        if status == STATUS_FAULTY:
            action = (
                f'<form method="post" action="{_REEVALUATE_ACTION}">'
                f'<input type="hidden" name="repository" '
                f'value="{_escape(manifest.name)}">'
                '<button type="submit">Reevaluate integrity</button>'
                "</form>"
            )
        else:
            action = ""
        rows.append(
            "<tr><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                _escape(manifest.name), status, action
            )
        )
    return (
        "<table>\n"
        "<tr><th>Repository</th><th>Status</th><th>Action</th></tr>\n"
        + "\n".join(rows)
        + "\n</table>"
    )


def _render_conflicts(manifests):
    """Conflicts table (repository, file, remote unit, description)."""
    rows = []
    for manifest in manifests:
        for issue in manifest.issues:
            if issue["type"] != "conflict":
                continue
            rows.append(
                "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                    _escape(manifest.name),
                    _escape(issue["path"]),
                    _escape(issue["remote"])
                    if issue["remote"] is not None
                    else "-",
                    _escape(issue["description"]),
                )
            )
    if not rows:
        return ""
    return (
        "<h2>Conflicts</h2>\n"
        "<table>\n"
        "<tr><th>Repository</th><th>File</th><th>Remote unit</th>"
        "<th>Description</th></tr>\n"
        + "\n".join(rows)
        + "\n</table>"
    )


def _render_remotes(remotes):
    """Known-units table (name, short status, last synchronization)."""
    if not remotes:
        return "<p>No other units are known.</p>"
    rows = []
    for name, status, last_sync in remotes:
        rows.append(
            "<tr><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                _escape(name),
                status,
                _escape(last_sync.isoformat())
                if last_sync is not None
                else "never",
            )
        )
    return (
        "<table>\n"
        "<tr><th>Unit</th><th>Status</th>"
        "<th>Last synchronization</th></tr>\n"
        + "\n".join(rows)
        + "\n</table>"
    )


def generate_summary(unit_identifier, uptime_seconds, manifests, remotes):
    """Render the HTML page that answers a GET to ``<unit_address>/summary``.

    ``unit_identifier`` is the unit name, ``uptime_seconds`` the system
    uptime in seconds, ``manifests`` the list of local
    :class:`Manifest` objects and ``remotes`` a list of
    ``(name, status, last sync time)`` tuples for the other known units
    (last sync time: :class:`datetime` or ``None``).

    The page shows the unit name, the uptime, the status of every
    managed repository, the files affected by conflicts and the units
    that caused them, a "Reevaluate integrity" button for every faulty
    repository (a POST form to ``/reevaluate`` carrying the repository
    name in a hidden ``repository`` field), and a short summary of the
    other known units.
    """
    if not isinstance(unit_identifier, str) or not unit_identifier.strip():
        raise ValueError(
            f"unit identifier must be a non-empty string, "
            f"got {unit_identifier!r}"
        )
    uptime = format_uptime(uptime_seconds)
    manifest_list = list(manifests)
    for manifest in manifest_list:
        if not isinstance(manifest, Manifest):
            raise ValueError(
                f"manifests must be a list of Manifest objects, "
                f"got {_type_name(manifest)}"
            )
    remotes = _validate_remotes(remotes)
    return _SUMMARY_TEMPLATE.format(
        title=_escape(unit_identifier),
        unit_name=_escape(unit_identifier),
        uptime=uptime,
        repositories=_render_repositories(manifest_list),
        conflicts=_render_conflicts(manifest_list),
        remotes=_render_remotes(remotes),
    )