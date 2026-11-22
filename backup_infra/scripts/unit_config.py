#!/usr/bin/env python3
"""Beacon unit config module.

``UnitConfig`` is a validated view of the YAML config file used by
``unit.py`` (see the "Config file" section of the project readme).

Both entry points -- the constructor from arguments and ``UnitConfig.load``
from a YAML file -- perform the same verification and raise ``ValueError``
on invalid input:

- ``port``               : integer network port to listen on (1-65535).
- ``discovery_port``     : optional integer UDP port used for network
  discovery (``None`` disables the network discovery protocol).
- ``discovery_address``  : optional address the discovery announcements are
  broadcast to (defaults to the limited broadcast address at run time).
- ``address``            : optional address to listen on.
- ``identifier``         : unit name, expected to be unique in the backup
  network.
- ``trusted_token``      : string used to verify that a peer belongs to the
  same backup network.
- ``sync_delay``         : optional integer, delay in seconds between
  synchronizations.
- ``sync_schedule``      : optional 5-field cron schedule for
  synchronizations.
- ``integrity_delay``    : optional integer, delay in seconds between
  integrity checks.
- ``integrity_schedule`` : optional 5-field cron schedule for integrity
  checks.
- ``repository``         : non-empty list of ``{name, path}`` entries with
  unique names.
- ``manifest_storage``   : local directory where manifest files are stored.
- ``fetched_file_mode``  : optional access mode of the files this unit writes
  when it fetches them from another unit (a number or a string of octal
  digits, ``0``-``7777`` octal).
- ``fetched_owner_id``   : optional numeric user id that owns the fetched
  files.
- ``fetched_group_id``   : optional numeric group id of the fetched files.
- ``max_transfer_size``  : optional integer, largest file (in bytes) a unit
  serves or accepts over a ``file`` request (``None``: no limit).
- ``transfer_timeout``   : optional integer number of seconds a ``file`` or
  ``status`` transfer may stay idle before it is given up (``None``: the unit
  applies ``DEFAULT_TRANSFER_TIMEOUT``).
- ``max_parallel_transfers`` : optional integer number of ``file`` transfers a
  unit runs at the same time, counting both directions (``None``: the unit
  applies ``DEFAULT_MAX_PARALLEL_TRANSFERS``).

``sync_delay`` and ``sync_schedule`` are mutually exclusive, but at least one
of them must be defined; the same applies to the ``integrity_delay`` /
``integrity_schedule`` pair.  Unknown fields are rejected.
"""

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

# Positional 5-field cron layout: (field name, minimum, maximum).  A field is
# either '*' or a number within the range; day of the week runs from
# 0 (Sunday) to 6 (Saturday).
_CRON_FIELDS = (
    ("minute", 0, 59),
    ("hour", 0, 23),
    ("day of the month", 1, 31),
    ("month", 1, 12),
    ("day of the week", 0, 6),
)

_INT_RE = re.compile(r"-?\d+")

#: A file access mode written as octal digits, e.g. ``"0644"``.
_OCTAL_RE = re.compile(r"[0-7]+")

#: Seconds a ``file`` or ``status`` transfer may stay idle when the config
#: does not set ``transfer_timeout``.  The transfer is given up (and retried
#: at the next synchronization) when a peer stops sending for that long.
DEFAULT_TRANSFER_TIMEOUT = 300

#: How many ``file`` transfers a unit runs at the same time when the config
#: does not set ``max_parallel_transfers``.
DEFAULT_MAX_PARALLEL_TRANSFERS = 2

#: Known top-level config fields (see the readme "Config file" section).
_CONFIG_FIELDS = (
    "port",
    "discovery_port",
    "discovery_address",
    "address",
    "identifier",
    "trusted_token",
    "sync_delay",
    "sync_schedule",
    "integrity_delay",
    "integrity_schedule",
    "repository",
    "manifest_storage",
    "fetched_file_mode",
    "fetched_owner_id",
    "fetched_group_id",
    "max_transfer_size",
    "transfer_timeout",
    "max_parallel_transfers",
)

#: Delay/schedule pairs that are mutually exclusive but jointly required.
_DELAY_SCHEDULE_PAIRS = (
    ("sync_delay", "sync_schedule"),
    ("integrity_delay", "integrity_schedule"),
)


def validate_schedule(schedule):
    """Validate a 5-field cron schedule string and return it unchanged.

    Format: ``minute hour day-of-month month day-of-week``.  Each field is
    either ``*`` or a number within the field's range (day of the week:
    0 is Sunday, 6 is Saturday).  Raises ``ValueError`` otherwise.
    """
    if not isinstance(schedule, str):
        raise ValueError(
            f"schedule must be a string, got {type(schedule).__name__}"
        )
    fields = schedule.split()
    if len(fields) != 5:
        raise ValueError(
            f"schedule {schedule!r} must have exactly 5 fields "
            f"(minute hour day month weekday), got {len(fields)}"
        )
    for token, (name, low, high) in zip(fields, _CRON_FIELDS):
        if token == "*":
            continue
        if _INT_RE.fullmatch(token) is None:
            raise ValueError(
                f"invalid {name} field {token!r} in schedule {schedule!r}: "
                "expected '*' or a number"
            )
        value = int(token)
        if not low <= value <= high:
            raise ValueError(
                f"{name} out of range in schedule {schedule!r}: {value} "
                f"(allowed {low}-{high})"
            )
    return schedule


# --------------------------------------------------------------- field checks


def _type_name(value):
    return type(value).__name__


def _validate_string(field, value):
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string, got {_type_name(value)}")
    if not value.strip():
        raise ValueError(f"{field} must not be empty")
    return value


def _validate_required_string(field, value):
    if value is None:
        raise ValueError(f"missing required field: {field}")
    return _validate_string(field, value)


def _validate_optional_string(field, value):
    if value is None:
        return None
    return _validate_string(field, value)


def _validate_port(port):
    if port is None:
        raise ValueError("missing required field: port")
    if isinstance(port, bool) or not isinstance(port, int):
        raise ValueError(f"port must be an integer, got {_type_name(port)}")
    if not 1 <= port <= 65535:
        raise ValueError(f"port must be in range 1-65535, got {port}")
    return port


def _validate_optional_port(field, value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer, got {_type_name(value)}")
    if not 1 <= value <= 65535:
        raise ValueError(f"{field} must be in range 1-65535, got {value}")
    return value


def _validate_delay(field, value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer, got {_type_name(value)}")
    if value <= 0:
        raise ValueError(f"{field} must be a positive integer, got {value}")
    return value


def _validate_positive_int(field, value, default=None):
    """Optional positive integer falling back to ``default`` when unset.

    ``None`` is always an accepted value: an optional field the config does
    not mention keeps reporting ``None`` (or ``default`` when the caller has
    one), so a saved config file never grows fields nobody asked for.
    """
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer, got {_type_name(value)}")
    if value <= 0:
        raise ValueError(f"{field} must be a positive integer, got {value}")
    return value


def validate_mode(field, value):
    """Optional file access mode, given as a number or as octal digits.

    ``0o644``, ``0644`` and ``"644"`` all describe the same mode: YAML turns
    a number with a leading zero into octal, so a string of digits is read as
    octal too instead of being silently taken as decimal.
    """
    if value is None:
        return None
    if isinstance(value, str):
        digits = value.strip()
        if digits[:2].lower() == "0o":
            digits = digits[2:]
        if not digits or _OCTAL_RE.fullmatch(digits) is None:
            raise ValueError(
                f"{field} must be an octal mode like 0644, got {value!r}"
            )
        mode = int(digits, 8)
    elif isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be a mode, got {_type_name(value)}")
    else:
        mode = value
    if not 0 <= mode <= 0o7777:
        raise ValueError(f"{field} must be in range 0-7777 octal, got {mode}")
    return mode


def validate_numeric_id(field, value):
    """Optional numeric user or group id.

    Only ids are accepted: the user and group databases inside the container
    are not the ones of the host, so the same name in two config files could
    mean two different accounts.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(
            f"{field} must be a numeric id, got {_type_name(value)}; "
            "user and group names are not supported"
        )
    if value < 0:
        raise ValueError(f"{field} must not be negative, got {value}")
    return value


def _validate_schedule_field(field, value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string, got {_type_name(value)}")
    validate_schedule(value)
    return value


def _validate_delay_schedule_pair(delay_field, delay, schedule_field, schedule):
    if delay is not None and schedule is not None:
        raise ValueError(
            f"{delay_field} and {schedule_field} are mutually exclusive"
        )
    if delay is None and schedule is None:
        raise ValueError(
            f"at least one of {delay_field} and {schedule_field} must be defined"
        )


@dataclass(frozen=True)
class Repository:
    """One entry of the ``repository`` config field."""

    name: str
    path: str


def _validate_repositories(value):
    if value is None:
        raise ValueError("missing required field: repository")
    if not isinstance(value, list):
        raise ValueError(f"repository must be a list, got {_type_name(value)}")
    if not value:
        raise ValueError("repository must contain at least one entry")
    repositories = []
    seen_names = set()
    for index, entry in enumerate(value):
        if isinstance(entry, Repository):
            name, path = entry.name, entry.path
        elif isinstance(entry, dict):
            unknown = [key for key in entry if key not in ("name", "path")]
            if unknown:
                raise ValueError(
                    f"repository[{index}] contains unknown field(s): "
                    + ", ".join(repr(key) for key in unknown)
                )
            name = entry.get("name")
            path = entry.get("path")
        else:
            raise ValueError(
                f"repository[{index}] must be a mapping or a Repository, "
                f"got {_type_name(entry)}"
            )
        prefix = f"repository[{index}]"
        name = _validate_required_string(f"{prefix}.name", name)
        path = _validate_required_string(f"{prefix}.path", path)
        if name in seen_names:
            raise ValueError(f"duplicate repository name: {name!r}")
        seen_names.add(name)
        repositories.append(Repository(name=name, path=path))
    return repositories


class UnitConfig:
    """Validated configuration of a Beacon unit.

    Built either from keyword arguments (``UnitConfig(port=..., ...)``) or
    from a YAML file (``UnitConfig.load(path)``); both entry points perform
    the same verification and raise ``ValueError`` on invalid input.  All
    fields are read-only and accessed through properties named after the
    config keys.
    """

    def __init__(
        self,
        *,
        port=None,
        discovery_port=None,
        discovery_address=None,
        address=None,
        identifier=None,
        trusted_token=None,
        sync_delay=None,
        sync_schedule=None,
        integrity_delay=None,
        integrity_schedule=None,
        repository=None,
        manifest_storage=None,
        fetched_file_mode=None,
        fetched_owner_id=None,
        fetched_group_id=None,
        max_transfer_size=None,
        transfer_timeout=None,
        max_parallel_transfers=None,
    ):
        self._port = _validate_port(port)
        self._discovery_port = _validate_optional_port(
            "discovery_port", discovery_port
        )
        self._discovery_address = _validate_optional_string(
            "discovery_address", discovery_address
        )
        self._address = _validate_optional_string("address", address)
        self._identifier = _validate_required_string("identifier", identifier)
        self._trusted_token = _validate_required_string(
            "trusted_token", trusted_token
        )
        self._sync_delay = _validate_delay("sync_delay", sync_delay)
        self._sync_schedule = _validate_schedule_field(
            "sync_schedule", sync_schedule
        )
        self._integrity_delay = _validate_delay("integrity_delay", integrity_delay)
        self._integrity_schedule = _validate_schedule_field(
            "integrity_schedule", integrity_schedule
        )
        self._repositories = _validate_repositories(repository)
        self._manifest_storage = _validate_required_string(
            "manifest_storage", manifest_storage
        )
        self._fetched_file_mode = validate_mode(
            "fetched_file_mode", fetched_file_mode
        )
        self._fetched_owner_id = validate_numeric_id(
            "fetched_owner_id", fetched_owner_id
        )
        self._fetched_group_id = validate_numeric_id(
            "fetched_group_id", fetched_group_id
        )
        self._max_transfer_size = _validate_positive_int(
            "max_transfer_size", max_transfer_size
        )
        self._transfer_timeout = _validate_positive_int(
            "transfer_timeout", transfer_timeout
        )
        self._max_parallel_transfers = _validate_positive_int(
            "max_parallel_transfers", max_parallel_transfers
        )

        for delay_field, schedule_field in _DELAY_SCHEDULE_PAIRS:
            _validate_delay_schedule_pair(
                delay_field,
                getattr(self, delay_field),
                schedule_field,
                getattr(self, schedule_field),
            )

    # ------------------------------------------------------------ accessors

    @property
    def port(self):
        """Network port to listen on (1-65535)."""
        return self._port

    @property
    def discovery_port(self):
        """UDP port used for network discovery (``None`` if not set)."""
        return self._discovery_port

    @property
    def discovery_address(self):
        """Broadcast address for discovery announcements (``None`` if not
        set; the unit falls back to the limited broadcast address)."""
        return self._discovery_address

    @property
    def address(self):
        """Optional address to listen on (``None`` if not set)."""
        return self._address

    @property
    def identifier(self):
        """Unit name; expected to be unique in the backup network."""
        return self._identifier

    @property
    def trusted_token(self):
        """Token used to verify peers belong to the same backup network."""
        return self._trusted_token

    @property
    def sync_delay(self):
        """Seconds between synchronizations (``None`` if a schedule is set)."""
        return self._sync_delay

    @property
    def sync_schedule(self):
        """Cron schedule for synchronizations (``None`` if a delay is set)."""
        return self._sync_schedule

    @property
    def integrity_delay(self):
        """Seconds between integrity checks (``None`` if a schedule is set)."""
        return self._integrity_delay

    @property
    def integrity_schedule(self):
        """Cron schedule for integrity checks (``None`` if a delay is set)."""
        return self._integrity_schedule

    @property
    def repository(self):
        """List of :class:`Repository` entries (a copy, safe to mutate)."""
        return list(self._repositories)

    @property
    def manifest_storage(self):
        """Local directory where manifest files are stored."""
        return self._manifest_storage

    @property
    def fetched_file_mode(self):
        """Access mode of the files written while fetching from another unit
        (``None`` if not set: the mode the operating system assigns)."""
        return self._fetched_file_mode

    @property
    def fetched_owner_id(self):
        """Numeric user id that owns fetched files (``None`` if not set)."""
        return self._fetched_owner_id

    @property
    def fetched_group_id(self):
        """Numeric group id of fetched files (``None`` if not set)."""
        return self._fetched_group_id

    @property
    def max_transfer_size(self):
        """Largest file (bytes) served or accepted over a ``file`` request
        (``None`` if not set: files of any size are transferred)."""
        return self._max_transfer_size

    @property
    def transfer_timeout(self):
        """Seconds a transfer may stay idle before it is given up (``None``
        if not set: the unit applies :data:`DEFAULT_TRANSFER_TIMEOUT`)."""
        return self._transfer_timeout

    @property
    def max_parallel_transfers(self):
        """Number of ``file`` transfers the unit runs at the same time
        (``None`` if not set: the unit applies
        :data:`DEFAULT_MAX_PARALLEL_TRANSFERS`)."""
        return self._max_parallel_transfers

    # ---------------------------------------------------------- construction

    @classmethod
    def load(cls, path):
        """Load and validate a unit config from the YAML file at ``path``.

        Raises ``ValueError`` for missing, unknown or invalid fields,
        ``yaml.YAMLError`` if the file is not valid YAML, and ``OSError``
        if the file cannot be read.
        """
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        if data is None:
            raise ValueError(f"empty config file: {path}")
        if not isinstance(data, dict):
            raise ValueError(
                "config root must be a mapping, got "
                f"{_type_name(data)}"
            )
        unknown = [key for key in data if key not in _CONFIG_FIELDS]
        if unknown:
            raise ValueError(
                "unknown config field(s): "
                + ", ".join(repr(key) for key in unknown)
            )
        return cls(**data)

    def __repr__(self):
        repos = ", ".join(f"{repo.name!r}" for repo in self._repositories)
        return (
            "UnitConfig(port=%r, discovery_port=%r, discovery_address=%r, "
            "address=%r, identifier=%r, "
            "trusted_token=<set>, sync_delay=%r, sync_schedule=%r, "
            "integrity_delay=%r, integrity_schedule=%r, repository=[%s], "
            "manifest_storage=%r, fetched_file_mode=%r, fetched_owner_id=%r, "
            "fetched_group_id=%r)"
            % (
                self._port,
                self._discovery_port,
                self._discovery_address,
                self._address,
                self._identifier,
                self._sync_delay,
                self._sync_schedule,
                self._integrity_delay,
                self._integrity_schedule,
                repos,
                self._manifest_storage,
                self._fetched_file_mode,
                self._fetched_owner_id,
                self._fetched_group_id,
            )
        )

