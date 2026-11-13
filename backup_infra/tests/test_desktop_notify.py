"""Tests for the desktop notification utility
(``scripts/desktop-notify.py``).

The utility requests ``/network`` from a unit and fires a desktop
notification via ``/usr/bin/notify-send``.  notify-send is not installed
in the test environment, so every test injects a fake notifier (a
callable that records the command).  The tests cover the message
content, the exact notify-send argument templates, the exit codes and
the live behavior against a local HTTP server (ok, faulty, malformed,
HTTP error, timeout, unreachable).
"""

import importlib.util
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from network_messages import (
    STATUS_CONFLICTS,
    STATUS_FAULTY,
    STATUS_OK,
    generate_network_status,
)


def _load_module():
    """Load scripts/desktop-notify.py (the hyphen blocks a plain import)."""
    path = (
        Path(__file__).resolve().parent.parent / "scripts" / "desktop-notify.py"
    )
    spec = importlib.util.spec_from_file_location("desktop_notify", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


desktop_notify = _load_module()


class FakeNotifier:
    """Records notify-send invocations instead of executing them."""

    def __init__(self):
        self.commands = []

    def __call__(self, command):
        self.commands.append(list(command))
        return 0


# ------------------------------------------------------------ arg template


def test_build_notify_args_success():
    assert desktop_notify.build_notify_args(True, "body") == [
        "/usr/bin/notify-send",
        "-t",
        "43200000",
        "-i",
        "/usr/custom/scripts/ok.png",
        "SYSTEM OP",
        "body",
    ]


def test_build_notify_args_failure():
    assert desktop_notify.build_notify_args(False, "body") == [
        "/usr/bin/notify-send",
        "-t",
        "43200000",
        "-i",
        "/usr/custom/scripts/error.png",
        "PROBLEMS DETECTED",
        "body",
    ]


def test_send_notification_uses_template():
    notifier = FakeNotifier()
    assert (
        desktop_notify.send_notification(True, "body", notifier=notifier)
        == 0
    )
    assert notifier.commands[0] == desktop_notify.build_notify_args(
        True, "body"
    )


def test_send_notification_survives_missing_notify_send(capsys):
    def broken(command):
        raise OSError("no such file or directory")

    assert (
        desktop_notify.send_notification(True, "body", notifier=broken)
        is None
    )
    assert "/usr/bin/notify-send" in capsys.readouterr().err


# ---------------------------------------------------------------- message


def test_format_unit_line():
    assert (
        desktop_notify.format_unit_line(
            "laptop", STATUS_OK, datetime(2026, 11, 10, 8, 15)
        )
        == "laptop: OK (last sync: 2026-11-10 08:15)"
    )
    assert (
        desktop_notify.format_unit_line("laptop", STATUS_OK, None)
        == "laptop: OK (last sync: never)"
    )


def test_format_status_message_all_ok():
    units = [
        ("laptop", STATUS_OK, datetime(2026, 11, 10, 8, 15)),
        ("pc", STATUS_OK, None),
    ]
    assert desktop_notify.format_status_message(units) == (
        "backup network: all 2 unit(s) OK\n"
        "\n"
        "laptop: OK (last sync: 2026-11-10 08:15)\n"
        "pc: OK (last sync: never)"
    )


def test_format_status_message_not_all_ok():
    units = [
        ("laptop", STATUS_FAULTY, datetime(2026, 11, 10, 8, 15)),
        ("pc", STATUS_OK, None),
    ]
    assert desktop_notify.format_status_message(units) == (
        "backup network: 1 of 2 unit(s) not OK\n"
        "\n"
        "laptop: faulty (last sync: 2026-11-10 08:15)\n"
        "pc: OK (last sync: never)"
    )


def test_format_status_message_single_conflicts_unit():
    message = desktop_notify.format_status_message(
        [("laptop", STATUS_CONFLICTS, None)]
    )
    assert "1 of 1 unit(s) not OK" in message
    assert "laptop: conflicts (last sync: never)" in message


def test_format_failure_message():
    assert desktop_notify.format_failure_message(
        "http://127.0.0.1:8090",
        "connection failed: [Errno 111] Connection refused",
    ) == (
        "backup network: could not get status from "
        "http://127.0.0.1:8090\n"
        "\n"
        "connection failed: [Errno 111] Connection refused"
    )


# -------------------------------------------------------------------- main


def test_main_all_ok(monkeypatch):
    notifier = FakeNotifier()
    monkeypatch.setattr(
        desktop_notify,
        "fetch_network_status",
        lambda unit, timeout: [("laptop", STATUS_OK, None)],
    )
    assert (
        desktop_notify.main(["--unit", "http://unit"], notifier=notifier)
        == 0
    )
    (command,) = notifier.commands
    assert command[0] == "/usr/bin/notify-send"
    assert command[1:3] == ["-t", "43200000"]
    assert command[3:5] == ["-i", "/usr/custom/scripts/ok.png"]
    assert command[5] == "SYSTEM OP"
    body = command[6]
    assert "all 1 unit(s) OK" in body
    assert "laptop: OK" in body
    assert "\n" in body  # the body is multiline plain text


def test_main_not_all_ok(monkeypatch):
    notifier = FakeNotifier()
    monkeypatch.setattr(
        desktop_notify,
        "fetch_network_status",
        lambda unit, timeout: [
            ("laptop", STATUS_FAULTY, None),
            ("pc", STATUS_OK, None),
        ],
    )
    assert (
        desktop_notify.main(["--unit", "http://unit"], notifier=notifier)
        == 1
    )
    (command,) = notifier.commands
    assert command[4] == "/usr/custom/scripts/error.png"
    assert command[5] == "PROBLEMS DETECTED"
    assert "1 of 2 unit(s) not OK" in command[6]
    assert "laptop: faulty" in command[6]


def test_main_request_failed(monkeypatch):
    notifier = FakeNotifier()

    def boom(unit, timeout):
        raise desktop_notify.RequestError(
            "connection failed: [Errno 111] Connection refused"
        )

    monkeypatch.setattr(desktop_notify, "fetch_network_status", boom)
    assert (
        desktop_notify.main(["--unit", "http://unit"], notifier=notifier)
        == 2
    )
    (command,) = notifier.commands
    assert command[4] == "/usr/custom/scripts/error.png"
    assert command[5] == "PROBLEMS DETECTED"
    # the failure is clearly shown in the message body
    assert "could not get status from http://unit" in command[6]
    assert "Connection refused" in command[6]


def test_main_empty_response(monkeypatch):
    notifier = FakeNotifier()
    monkeypatch.setattr(
        desktop_notify, "fetch_network_status", lambda unit, timeout: []
    )
    assert (
        desktop_notify.main(["--unit", "http://unit"], notifier=notifier)
        == 2
    )
    (command,) = notifier.commands
    assert command[5] == "PROBLEMS DETECTED"
    assert "no units reported" in command[6]


def test_main_unreachable_unit():
    notifier = FakeNotifier()
    # port 1 (tcpmux) is closed on any sensible system
    assert (
        desktop_notify.main(
            ["--unit", "http://127.0.0.1:1", "--timeout", "2"],
            notifier=notifier,
        )
        == 2
    )
    (command,) = notifier.commands
    assert command[5] == "PROBLEMS DETECTED"
    assert "could not get status from http://127.0.0.1:1" in command[6]
    assert "connection failed" in command[6]


# ------------------------------------------------------- live server tests


class _Handler(BaseHTTPRequestHandler):
    """Serves ``/network`` (and anything else) with a fixed payload."""

    unit_status = STATUS_OK
    raw_body = None
    http_status = 200

    def do_GET(self):
        if self.raw_body is not None:
            body = self.raw_body
        else:
            body = generate_network_status(
                [("laptop", self.unit_status, datetime(2026, 11, 10, 8, 15))]
            ).encode("utf-8")
        self.send_response(self.http_status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def unit_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()
    thread.join()


def test_main_live_unit_ok(unit_server):
    notifier = FakeNotifier()
    assert (
        desktop_notify.main(["--unit", unit_server], notifier=notifier) == 0
    )
    (command,) = notifier.commands
    assert command[4] == "/usr/custom/scripts/ok.png"
    assert command[5] == "SYSTEM OP"
    body = command[6]
    assert "all 1 unit(s) OK" in body
    assert "laptop: OK (last sync: 2026-11-10 08:15)" in body


def test_main_live_unit_faulty(unit_server, monkeypatch):
    monkeypatch.setattr(_Handler, "unit_status", STATUS_FAULTY)
    notifier = FakeNotifier()
    assert (
        desktop_notify.main(["--unit", unit_server], notifier=notifier) == 1
    )
    (command,) = notifier.commands
    assert command[4] == "/usr/custom/scripts/error.png"
    assert command[5] == "PROBLEMS DETECTED"
    assert "1 of 1 unit(s) not OK" in command[6]
    assert "laptop: faulty (last sync: 2026-11-10 08:15)" in command[6]


def test_main_live_unit_malformed_response(unit_server, monkeypatch):
    monkeypatch.setattr(_Handler, "raw_body", b"this is not json")
    notifier = FakeNotifier()
    assert (
        desktop_notify.main(["--unit", unit_server], notifier=notifier) == 2
    )
    (command,) = notifier.commands
    assert command[5] == "PROBLEMS DETECTED"
    assert "malformed response" in command[6]


def test_main_live_unit_http_error(unit_server, monkeypatch):
    monkeypatch.setattr(_Handler, "http_status", 500)
    monkeypatch.setattr(_Handler, "raw_body", b"internal error")
    notifier = FakeNotifier()
    assert (
        desktop_notify.main(["--unit", unit_server], notifier=notifier) == 2
    )
    (command,) = notifier.commands
    assert command[5] == "PROBLEMS DETECTED"
    assert "HTTP 500" in command[6]


def test_main_request_timeout():
    class _SlowHandler(_Handler):
        def do_GET(self):
            time.sleep(2)
            super().do_GET()

    server = ThreadingHTTPServer(("127.0.0.1", 0), _SlowHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        notifier = FakeNotifier()
        assert (
            desktop_notify.main(
                [
                    "--unit",
                    f"http://127.0.0.1:{server.server_address[1]}",
                    "--timeout",
                    "0.3",
                ],
                notifier=notifier,
            )
            == 2
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    (command,) = notifier.commands
    assert command[5] == "PROBLEMS DETECTED"
    assert "timed out" in command[6]


