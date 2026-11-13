#!/usr/bin/env python3
"""Beacon desktop notification utility (desktop_notify).

``desktop-notify.py`` requests the ``network`` status from a given unit
(``GET <unit>/network``, message format defined by
``network_messages``), prepares a multi-line plain text message from the
response and fires it as a desktop notification by calling
``/usr/bin/notify-send``.  It is meant to be run from cron (the
installation wizard can add a cron entry for it).

The utility always fires exactly one notification:

- the request succeeded and every reported unit has the "OK" status --
  a success notification with the argument template

      -t 43200000 -i /usr/custom/scripts/ok.png "SYSTEM OP" "<message body>"

- the request succeeded but at least one reported unit does not have
  the "OK" status -- a failure notification with the argument template

      -t 43200000 -i /usr/custom/scripts/error.png "PROBLEMS DETECTED" "<message body>"

- the request failed (unreachable unit, timeout, HTTP error) or the
  response is unusable (malformed or empty) -- a failure notification
  whose body clearly shows what went wrong.

The message body is plain text (no markup) with one line per unit.

Exit codes:

- 0 -- every reported unit is OK
- 1 -- at least one reported unit is not OK
- 2 -- no usable status (request failed, malformed or empty response)

Usage:
    desktop-notify.py [--unit URL] [--timeout SECONDS]
"""

import argparse
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from network_messages import STATUS_OK, parse_network_status  # noqa: E402

#: Default address of the unit to query.
DEFAULT_UNIT = "http://127.0.0.1:8090"

#: Default request timeout in seconds.
DEFAULT_TIMEOUT = 5.0

#: notify-send invocation settings (the argument templates are fixed).
NOTIFY_SEND_PATH = "/usr/bin/notify-send"
NOTIFY_TIMEOUT_MS = "43200000"
NOTIFY_ICON_OK = "/usr/custom/scripts/ok.png"
NOTIFY_ICON_ERROR = "/usr/custom/scripts/error.png"
NOTIFY_TITLE_SUCCESS = "SYSTEM OP"
NOTIFY_TITLE_FAILURE = "PROBLEMS DETECTED"


class RequestError(Exception):
    """The network status could not be fetched or decoded.

    ``reason`` is a human readable description used in the notification
    message.
    """

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


# ----------------------------------------------------------------- request


def _describe_connection_error(error, timeout):
    """A short human readable description of a connection error."""
    reason = getattr(error, "reason", error)
    if isinstance(reason, TimeoutError):
        return f"request timed out after {timeout:g} s"
    return f"connection failed: {reason}"


def fetch_network_status(unit, timeout):
    """Fetch and decode the network status from ``unit``.

    Returns the list of ``(name, status, last sync time)`` tuples.
    Raises :class:`RequestError` (with a human readable ``reason``) when
    the request fails, the HTTP status is an error, or the response does
    not match the network status format.
    """
    url = unit.rstrip("/") + "/network"
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url), timeout=timeout
        ) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        raise RequestError(
            f"HTTP {error.code} ({error.reason}) from {url}"
        ) from error
    except OSError as error:
        raise RequestError(
            _describe_connection_error(error, timeout)
        ) from error
    try:
        return parse_network_status(body)
    except ValueError as error:
        raise RequestError(f"malformed response: {error}") from error


# ----------------------------------------------------------------- message


def format_unit_line(name, status, last_sync):
    """One plain text line for a single unit."""
    if last_sync is None:
        sync = "never"
    else:
        sync = last_sync.strftime("%Y-%m-%d %H:%M")
    return f"{name}: {status} (last sync: {sync})"


def format_status_message(units):
    """The plain text message body for a decoded network status.

    ``units`` is a list of ``(name, status, last sync time)`` tuples.
    """
    total = len(units)
    not_ok = [name for name, status, _sync in units if status != STATUS_OK]
    if not not_ok:
        header = f"backup network: all {total} unit(s) OK"
    else:
        header = f"backup network: {len(not_ok)} of {total} unit(s) not OK"
    lines = [header, ""]
    lines.extend(
        format_unit_line(name, status, last_sync)
        for name, status, last_sync in units
    )
    return "\n".join(lines)


def format_failure_message(unit, reason):
    """The plain text message body for a failed or unusable request."""
    return f"backup network: could not get status from {unit}\n\n{reason}"


# ------------------------------------------------------------ notification


def build_notify_args(success, body):
    """The notify-send argument template for the notification.

    Success:
    ``-t 43200000 -i /usr/custom/scripts/ok.png "SYSTEM OP" "<body>"``

    Failure:
    ``-t 43200000 -i /usr/custom/scripts/error.png "PROBLEMS DETECTED" "<body>"``
    """
    if success:
        icon = NOTIFY_ICON_OK
        title = NOTIFY_TITLE_SUCCESS
    else:
        icon = NOTIFY_ICON_ERROR
        title = NOTIFY_TITLE_FAILURE
    return [
        NOTIFY_SEND_PATH,
        "-t",
        NOTIFY_TIMEOUT_MS,
        "-i",
        icon,
        title,
        body,
    ]


def send_notification(success, body, notifier=None):
    """Fire the desktop notification for ``body``.

    ``notifier`` is the callable that executes the command; it defaults
    to :func:`subprocess.run` and can be replaced in tests.  A missing
    or failing notify-send is reported on stderr and never aborts the
    utility.  Returns the notifier result (or ``None``).
    """
    if notifier is None:
        notifier = subprocess.run
    try:
        return notifier(build_notify_args(success, body))
    except OSError as error:
        print(
            f"error: cannot run {NOTIFY_SEND_PATH}: {error}",
            file=sys.stderr,
        )
        return None


# --------------------------------------------------------------------- main


def main(argv=None, notifier=None):
    parser = argparse.ArgumentParser(
        description="send a backup network status desktop notification"
    )
    parser.add_argument(
        "--unit",
        default=DEFAULT_UNIT,
        help="address of the unit to query (default: %(default)s)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help="request timeout in seconds (default: %(default)s)",
    )
    args = parser.parse_args(argv)

    try:
        units = fetch_network_status(args.unit, args.timeout)
    except RequestError as error:
        send_notification(
            False, format_failure_message(args.unit, error.reason), notifier
        )
        return 2

    if not units:
        send_notification(
            False,
            format_failure_message(
                args.unit, "no units reported in the response"
            ),
            notifier,
        )
        return 2

    all_ok = all(status == STATUS_OK for _name, status, _sync in units)
    send_notification(all_ok, format_status_message(units), notifier)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())

