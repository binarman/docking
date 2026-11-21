"""Tests for the network discovery protocol (``scripts/unit.py``).

Covers the "Network discovery protocol" section of
``docs/software_architecture.md`` by running several units on a single
machine: the LAN broadcast address is replaced by the loopback address
and the UDP ports are OS-assigned free high ports (8080/8081 are left
alone).

- root/leaf mode selection and the leaf port scan (error when no port
  is free)
- announcement exchange, root forwarding to known local units, the
  trusted-token check
- peer expiry (liveness), malformed datagrams, the discovered remotes
  in the unit state and the ``/network`` endpoint
- the debug-level trace of what is announced, what is received and what
  a root decides to forward (``unit.py --log-level debug``)
"""

import asyncio
import logging
import socket
import time

import pytest

import unit as unit_module
from network_messages import parse_network_status
from unit import Discovery, Unit
from unit_config import UnitConfig


# ------------------------------------------------------------------- helpers


def make_unit(tmp_path, identifier, port, discovery_port, trusted_token="token"):
    """A unit configured for discovery over the loopback interface."""
    repo_root = tmp_path / identifier
    repo_root.mkdir(parents=True, exist_ok=True)
    config = UnitConfig(
        port=port,
        discovery_port=discovery_port,
        discovery_address="127.0.0.1",
        identifier=identifier,
        trusted_token=trusted_token,
        sync_delay=3600,
        integrity_delay=3600,
        repository=[{"name": "repo", "path": str(repo_root)}],
        manifest_storage=str(tmp_path / "manifests"),
    )
    unit = Unit(config)
    unit.load_manifests()
    return unit


def free_port(kind=socket.SOCK_DGRAM):
    """An OS-assigned free UDP/TCP port (a high ephemeral port)."""
    with socket.socket(socket.AF_INET, kind) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def free_tcp_port():
    return free_port(socket.SOCK_STREAM)


def hold_port(port):
    """Return a bound UDP socket occupying ``port`` (caller closes it)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", port))
    return sock


def free_port_run(count):
    """A run of ``count`` consecutive currently-free UDP ports.

    Returns ``(base port, sockets)``: the ports ``base`` .. ``base +
    count - 1`` are held by the returned sockets, so a test can close
    them (or part of them) and know exactly which ports are free.  The
    run is found by trial, which keeps the port numbers predictable
    without assuming the machine has no other UDP port in use.
    """
    for _ in range(200):
        base = free_port()
        if base + count > 65535:
            continue
        socks = []
        try:
            for offset in range(count):
                socks.append(hold_port(base + offset))
        except OSError:
            for sock in socks:
                sock.close()
            continue
        return base, socks
    raise AssertionError(f"no run of {count} free UDP ports found")


def release(socks):
    """Close the sockets returned by :func:`free_port_run`."""
    for sock in socks:
        sock.close()


def make_discovery(unit, **overrides):
    """A Discovery with fast test timings around ``unit``."""
    kwargs = {"announce_interval": 0.05, "expire_after": 5.0}
    kwargs.update(overrides)
    return Discovery(unit, **kwargs)


async def wait_for(condition, what, timeout=5.0):
    """Poll ``condition`` until it holds or the timeout expires."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if condition():
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"condition not met in {timeout}s: {what}")


def known_ids(discovery):
    return {entry[0] for entry in discovery.known_units()}


def known_entry(discovery, unit_id):
    entries = [entry for entry in discovery.known_units() if entry[0] == unit_id]
    assert len(entries) == 1, entries
    return entries[0]


# -------------------------------------------------------------- mode selection


def test_first_unit_becomes_root(tmp_path):
    async def scenario():
        base = free_port()
        root = make_discovery(
            make_unit(tmp_path, "root-unit", free_tcp_port(), base)
        )
        await root.start()
        try:
            assert root.mode == "root"
            assert root.udp_port == base
        finally:
            await root.stop()

    asyncio.run(scenario())


def test_second_unit_becomes_leaf(tmp_path):
    async def scenario():
        base, socks = free_port_run(2)
        release(socks)
        root = make_discovery(
            make_unit(tmp_path, "root-unit", free_tcp_port(), base)
        )
        leaf = make_discovery(
            make_unit(tmp_path, "leaf-unit", free_tcp_port(), base)
        )
        await root.start()
        await leaf.start()
        try:
            assert leaf.mode == "leaf"
            assert leaf.udp_port == base + 1
        finally:
            await leaf.stop()
            await root.stop()

    asyncio.run(scenario())


def test_leaf_scans_to_a_further_free_port(tmp_path):
    async def scenario():
        base, socks = free_port_run(3)
        socks[2].close()  # leave base and base + 1 occupied
        discovery = make_discovery(
            make_unit(tmp_path, "unit-a", free_tcp_port(), base),
            port_scan_limit=8,
        )
        await discovery.start()
        try:
            assert discovery.mode == "leaf"
            assert discovery.udp_port == base + 2
        finally:
            await discovery.stop()
            release(socks)

    asyncio.run(scenario())


def test_error_when_no_port_is_free(tmp_path):
    async def scenario():
        base, socks = free_port_run(4)
        discovery = make_discovery(
            make_unit(tmp_path, "unit-a", free_tcp_port(), base),
            port_scan_limit=3,
        )
        try:
            with pytest.raises(OSError, match="no free UDP discovery port"):
                await discovery.start()
            assert discovery.mode is None
        finally:
            release(socks)

    asyncio.run(scenario())


# ---------------------------------------------------------------- liveness


def test_silent_unit_is_forgotten(tmp_path):
    async def scenario():
        base = free_port()
        root = make_discovery(
            make_unit(tmp_path, "root-unit", free_tcp_port(), base),
            announce_interval=0.05,
            expire_after=0.2,
        )
        leaf_unit = make_unit(tmp_path, "leaf-unit", free_tcp_port(), base)
        leaf = make_discovery(leaf_unit)
        await root.start()
        await leaf.start()
        try:
            await wait_for(
                lambda: known_ids(root) == {"leaf-unit"}, "root knows the leaf"
            )
            assert [name for name, _, _ in root._unit.remotes] == ["leaf-unit"]
            # the leaf stops announcing: the root forgets it again
            await leaf.stop()
            await wait_for(
                lambda: known_ids(root) == set(), "root forgot the leaf"
            )
            assert root._unit.remotes == []
        finally:
            await leaf.stop()
            await root.stop()

    asyncio.run(scenario())


def test_expire_peers_reports_nothing_for_a_live_peer(tmp_path):
    async def scenario():
        base = free_port()
        root = make_discovery(
            make_unit(tmp_path, "root-unit", free_tcp_port(), base),
            expire_after=60.0,
        )
        leaf = make_discovery(
            make_unit(tmp_path, "leaf-unit", free_tcp_port(), base)
        )
        await root.start()
        await leaf.start()
        try:
            await wait_for(
                lambda: known_ids(root) == {"leaf-unit"}, "root knows the leaf"
            )
            assert root.expire_peers() == 0
            assert known_ids(root) == {"leaf-unit"}
        finally:
            await leaf.stop()
            await root.stop()

    asyncio.run(scenario())


# ------------------------------------------------------------- broken traffic


class _FakeTransport:
    """A UDP transport that only records what would be sent."""

    def __init__(self):
        self.sent = []

    def sendto(self, data, addr):
        self.sent.append((data, addr))

    def close(self):
        pass


def test_malformed_datagrams_are_ignored(tmp_path, caplog):
    async def scenario():
        discovery = make_discovery(
            make_unit(tmp_path, "unit-a", free_tcp_port(), 45000)
        )
        discovery._transport = _FakeTransport()
        discovery._udp_port = 45000
        discovery._mode = "root"
        with caplog.at_level(logging.DEBUG, logger="beacon.unit"):
            discovery.handle_datagram(b"not json at all", ("127.0.0.1", 45001))
            discovery.handle_datagram(b"\xff\xfe broken", ("127.0.0.1", 45001))
            discovery.handle_datagram(
                b'{"unit_id": "ghost", "unit_port": 1}', ("127.0.0.1", 45001)
            )
        await asyncio.sleep(0)
        assert known_ids(discovery) == set()
        assert discovery._unit.remotes == []
        assert "ignoring datagram" in caplog.text

    asyncio.run(scenario())


def test_own_announcement_is_ignored(tmp_path, caplog):
    async def scenario():
        unit = make_unit(tmp_path, "unit-a", free_tcp_port(), 45000)
        discovery = make_discovery(unit)
        discovery._transport = _FakeTransport()
        discovery._udp_port = 45000
        discovery._mode = "root"
        message = unit_module.generate_discovery(
            "unit-a", 45000, unit.config.port, "token"
        )
        with caplog.at_level(logging.DEBUG, logger="beacon.unit"):
            discovery.handle_datagram(
                message.encode("utf-8"), ("127.0.0.1", 45000)
            )
        assert known_ids(discovery) == set()
        assert unit.remotes == []
        assert "it is this unit itself" in caplog.text

    asyncio.run(scenario())


# --------------------------------------------------------------- announcements


def test_root_and_leaf_exchange_announcements(tmp_path):
    async def scenario():
        base = free_port()
        root_unit = make_unit(tmp_path, "root-unit", free_tcp_port(), base)
        leaf_unit = make_unit(tmp_path, "leaf-unit", free_tcp_port(), base)
        root = make_discovery(root_unit)
        leaf = make_discovery(leaf_unit)
        await root.start()
        await leaf.start()
        try:
            # the root hears the leaf's broadcast announcements
            await wait_for(
                lambda: known_ids(root) == {"leaf-unit"}, "root knows the leaf"
            )
            _, address, udp_port, tcp_port, local = known_entry(
                root, "leaf-unit"
            )
            assert address == "127.0.0.1"
            assert udp_port == leaf.udp_port
            assert tcp_port == leaf_unit.config.port
            assert local is True
            # the leaf learns the root through the root's re-sent
            # announcement (leaves do not listen on the shared port)
            await wait_for(
                lambda: known_ids(leaf) == {"root-unit"}, "leaf knows the root"
            )
            _, _, udp_port, tcp_port, local = known_entry(leaf, "root-unit")
            assert udp_port == base
            assert tcp_port == root_unit.config.port
            assert local is True
        finally:
            await leaf.stop()
            await root.stop()

    asyncio.run(scenario())


def test_root_forwards_between_leaves(tmp_path):
    async def scenario():
        base, socks = free_port_run(3)
        release(socks)
        root = make_discovery(
            make_unit(tmp_path, "root-unit", free_tcp_port(), base)
        )
        leaf_1 = make_discovery(
            make_unit(tmp_path, "leaf-1", free_tcp_port(), base)
        )
        leaf_2 = make_discovery(
            make_unit(tmp_path, "leaf-2", free_tcp_port(), base)
        )
        await root.start()
        await leaf_1.start()
        await leaf_2.start()
        try:
            assert leaf_1.udp_port == base + 1
            assert leaf_2.udp_port == base + 2
            # every leaf learns the other leaf only through the root's
            # resends (a leaf never forwards anything)
            await wait_for(
                lambda: known_ids(leaf_1) == {"root-unit", "leaf-2"},
                "leaf-1 knows leaf-2",
            )
            await wait_for(
                lambda: known_ids(leaf_2) == {"root-unit", "leaf-1"},
                "leaf-2 knows leaf-1",
            )
            assert known_ids(root) == {"leaf-1", "leaf-2"}
        finally:
            for discovery in (leaf_1, leaf_2, root):
                await discovery.stop()

    asyncio.run(scenario())


def test_mismatched_token_is_ignored(tmp_path, caplog):
    async def scenario():
        base = free_port()
        root = make_discovery(
            make_unit(tmp_path, "root-unit", free_tcp_port(), base)
        )
        intruder = make_discovery(
            make_unit(
                tmp_path,
                "intruder",
                free_tcp_port(),
                base,
                trusted_token="other network token",
            )
        )
        await root.start()
        await intruder.start()
        try:
            with caplog.at_level(logging.DEBUG, logger="beacon.unit"):
                await asyncio.sleep(0.3)  # several announce intervals
            assert known_ids(root) == set()
            assert known_ids(intruder) == set()
            assert root._unit.remotes == []
            assert "mismatched trusted_token" in caplog.text
        finally:
            await intruder.stop()
            await root.stop()

    asyncio.run(scenario())


# ------------------------------------------------ unit state and /network


def test_discovered_units_are_reported_as_remotes(tmp_path):
    async def scenario():
        base = free_port()
        unit = make_unit(tmp_path, "unit-a", free_tcp_port(), base)
        discovery = make_discovery(unit)
        discovery._transport = _FakeTransport()
        discovery._udp_port = base
        discovery._mode = "root"
        message = unit_module.generate_discovery(
            "unit-b", base + 1, 18090, "token"
        )
        discovery.handle_datagram(message.encode("utf-8"), ("127.0.0.1", base))
        # registered once; a refreshed announcement does not duplicate it
        discovery.handle_datagram(message.encode("utf-8"), ("127.0.0.1", base))
        assert unit.remotes == [("unit-b", "OK", None)]

        # the /network endpoint reports the discovered remote
        server = await asyncio.start_server(
            lambda reader, writer: unit_module._handle_connection(
                unit, reader, writer
            ),
            "127.0.0.1",
            unit.config.port,
        )
        try:
            reader, writer = await asyncio.open_connection(
                "127.0.0.1", unit.config.port
            )
            try:
                writer.write(
                    b"GET /network HTTP/1.1\r\nHost: x\r\n"
                    b"Connection: close\r\n\r\n"
                )
                await writer.drain()
                payload = await reader.read()
            finally:
                writer.close()
                await writer.wait_closed()
        finally:
            server.close()
            await server.wait_closed()
        header, _, body = payload.partition(b"\r\n\r\n")
        assert b" 200 OK" in header
        units = parse_network_status(body.decode("utf-8"))
        # the unit itself first, then the discovered remote
        assert [name for name, _, _ in units] == ["unit-a", "unit-b"]
        assert units[1] == ("unit-b", "OK", None)

    asyncio.run(scenario())


def test_sync_reports_known_peers(tmp_path, caplog):
    async def scenario():
        unit = make_unit(tmp_path, "unit-a", free_tcp_port(), 45000)
        assert unit.register_remote("unit-b") is True
        assert unit.register_remote("unit-b") is False
        assert unit.register_remote("unit-c") is True
        unit.forget_remote("unit-c")
        unit.forget_remote("unknown")
        assert [name for name, _, _ in unit.remotes] == ["unit-b"]
        with caplog.at_level(logging.INFO, logger="beacon.unit"):
            await unit.sync()
        assert "1 peer(s) known (unit-b)" in caplog.text

    asyncio.run(scenario())


def test_discovery_requires_a_configured_port(tmp_path):
    async def scenario():
        repo_root = tmp_path / "unit-a"
        repo_root.mkdir()
        config = UnitConfig(
            port=free_tcp_port(),
            identifier="unit-a",
            trusted_token="token",
            sync_delay=3600,
            integrity_delay=3600,
            repository=[{"name": "repo", "path": str(repo_root)}],
            manifest_storage=str(tmp_path / "manifests"),
        )
        unit = Unit(config)
        discovery = make_discovery(unit)
        with pytest.raises(
            ValueError, match="discovery_port is not configured"
        ):
            await discovery.start()

    asyncio.run(scenario())


# ---------------------------------------------------------------- log details


def make_offline_discovery(
    tmp_path, identifier="unit-a", udp_port=45000, mode="root", **overrides
):
    """A discovery that never bound a socket: fake transport, fixed mode.

    Enough of a started unit to call :meth:`Discovery.announce`,
    :meth:`Discovery.handle_datagram` and :meth:`Discovery._forward`
    without touching the network.  Extra keyword arguments are passed to
    :func:`make_discovery` (for instance ``local_addresses``).
    """
    unit = make_unit(tmp_path, identifier, free_tcp_port(), udp_port)
    discovery = make_discovery(unit, **overrides)
    discovery._transport = _FakeTransport()
    discovery._udp_port = udp_port
    discovery._mode = mode
    return discovery


def add_peer(
    discovery, unit_id, address="127.0.0.1", unit_port=45001, local=True
):
    """Add a known peer to ``discovery`` without running the protocol."""
    discovery._peers[unit_id] = {
        "address": address,
        "unit_port": unit_port,
        "tcp_port": 18090,
        "local": local,
        "last_seen": time.monotonic(),
    }


def test_announcement_is_logged(tmp_path, caplog):
    async def scenario():
        discovery = make_offline_discovery(tmp_path)
        with caplog.at_level(logging.DEBUG, logger="beacon.unit"):
            await discovery.announce()
        assert (
            "announcing unit 'unit-a' (UDP port 45000, TCP port "
            in caplog.text
        )
        assert "to 127.0.0.1:45000 in root mode" in caplog.text
        assert (
            "forwarded its own announcement to 0 of 0 known peer(s)"
            in caplog.text
        )
        assert [addr for _, addr in discovery._transport.sent] == [
            ("127.0.0.1", 45000)
        ]

    asyncio.run(scenario())


def test_received_announcement_is_logged(tmp_path, caplog):
    async def scenario():
        discovery = make_offline_discovery(tmp_path)
        message = unit_module.generate_discovery(
            "unit-b", 45001, 18090, "token"
        )
        with caplog.at_level(logging.DEBUG, logger="beacon.unit"):
            discovery.handle_datagram(
                message.encode("utf-8"), ("127.0.0.1", 45001)
            )
        assert (
            f"{len(message)} byte datagram from 127.0.0.1:45001"
            in caplog.text
        )
        assert (
            "valid announcement of unit 'unit-b' from 127.0.0.1:45001 "
            "(UDP port 45001, TCP port 18090)" in caplog.text
        )
        # the only known peer is the one that announced: it is not sent
        # its own announcement back
        assert (
            "not forwarding to unit 'unit-b': the announcement came from "
            "it at 127.0.0.1:45001" in caplog.text
        )
        assert (
            "forwarded 127.0.0.1:45001 to 0 of 1 known peer(s)"
            in caplog.text
        )

    asyncio.run(scenario())


def test_forwarding_decisions_are_logged(tmp_path, caplog):
    discovery = make_offline_discovery(tmp_path)
    add_peer(discovery, "local-peer", unit_port=45001)
    add_peer(
        discovery,
        "other-host-peer",
        address="203.0.113.9",
        unit_port=45002,
        local=False,
    )
    add_peer(discovery, "origin-peer", unit_port=45003)
    with caplog.at_level(logging.DEBUG, logger="beacon.unit"):
        discovery._forward(
            b"announcement", ("127.0.0.1", 45003), "origin-peer"
        )
    assert "forwarded to unit 'local-peer' at 127.0.0.1:45001" in caplog.text
    assert (
        "not forwarding to unit 'other-host-peer': not on this host"
        in caplog.text
    )
    assert (
        "not forwarding to unit 'origin-peer': the announcement came from "
        "it at 127.0.0.1:45003" in caplog.text
    )
    assert "forwarded 127.0.0.1:45003 to 1 of 3 known peer(s)" in caplog.text
    # and indeed only the local peer received the announcement
    assert [addr for _, addr in discovery._transport.sent] == [
        ("127.0.0.1", 45001)
    ]


def test_leaf_logs_that_it_never_forwards(tmp_path, caplog):
    discovery = make_offline_discovery(tmp_path, mode="leaf")
    add_peer(discovery, "local-peer")
    with caplog.at_level(logging.DEBUG, logger="beacon.unit"):
        discovery._forward(
            b"announcement", ("127.0.0.1", 45001), "local-peer"
        )
    assert "nothing forwarded, this unit is a leaf" in caplog.text
    assert "forwarded to unit" not in caplog.text
    assert discovery._transport.sent == []


class _BrokenTransport(_FakeTransport):
    """A UDP transport whose sends always fail."""

    def sendto(self, data, addr):
        raise OSError("network is down")


def test_failed_forward_is_logged_and_not_counted(tmp_path, caplog):
    discovery = make_offline_discovery(tmp_path)
    discovery._transport = _BrokenTransport()
    add_peer(discovery, "local-peer", unit_port=45001)
    add_peer(discovery, "second-peer", unit_port=45002)
    with caplog.at_level(logging.DEBUG, logger="beacon.unit"):
        discovery._forward(b"announcement", None, None)
    assert (
        "cannot forward to unit 'local-peer' at 127.0.0.1:45001: "
        "network is down" in caplog.text
    )
    assert (
        "forwarded its own announcement to 0 of 2 known peer(s)"
        in caplog.text
    )


def test_local_addresses_are_logged_on_start(tmp_path, caplog):
    """The addresses that make an announcement come from this machine."""

    async def scenario():
        unit = make_unit(tmp_path, "unit-a", free_tcp_port(), free_port())
        discovery = make_discovery(
            unit, local_addresses={"192.0.2.9", "192.0.2.7"}
        )
        with caplog.at_level(logging.DEBUG, logger="beacon.unit"):
            await discovery.start()
        try:
            lines = [
                record.getMessage()
                for record in caplog.records
                if "local interface addresses" in record.getMessage()
            ]
        finally:
            await discovery.stop()
        assert len(lines) == 1, caplog.text
        # the addresses of the constructor, sorted, and nothing but those
        assert lines[0] == (
            "discovery: local interface addresses: 192.0.2.7, 192.0.2.9 "
            "(announcements coming from one of them are units of this "
            "machine)"
        )
        assert discovery._local_addresses == {"192.0.2.7", "192.0.2.9"}

    asyncio.run(scenario())


def test_local_or_remote_verdict_is_logged(tmp_path, caplog):
    """Why an announced unit counts as a unit of this machine or not."""
    discovery = make_offline_discovery(tmp_path, local_addresses={"192.0.2.7"})
    announcements = [
        ("unit-loopback", "127.0.0.1", "a loopback address"),
        ("unit-local-if", "192.0.2.7", "one of the local interface addresses"),
        ("unit-remote", "203.0.113.9", "not one of the local interface addresses"),
    ]
    with caplog.at_level(logging.DEBUG, logger="beacon.unit"):
        for unit_id, address, _ in announcements:
            message = unit_module.generate_discovery(
                unit_id, 45001, 18090, "token"
            )
            discovery.handle_datagram(message.encode("utf-8"), (address, 45001))
    for unit_id, address, reason in announcements:
        expected = f"unit {unit_id!r} announced from {address}: {reason}"
        assert expected in caplog.text, caplog.text
    # the verdict of the log lines is the one that is registered
    assert [(entry[0], entry[4]) for entry in discovery.known_units()] == [
        ("unit-local-if", True),
        ("unit-loopback", True),
        ("unit-remote", False),
    ]


