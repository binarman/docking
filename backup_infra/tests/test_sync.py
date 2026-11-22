"""Tests for the unit-to-unit transfer and the synchronization round.

Covers the "File request" section of ``docs/software_architecture.md`` and the
"Algorithm" section of ``docs/synchronization.md`` with two units running on
this machine:

- serving a tracked file over ``GET /file``: the token, path, manifest,
  repository state and size guards, and the streamed content
- the client-side response helpers (head, body, streamed file, publishing)
- fetching one file into a repository: verification, atomic placement and
  cleanup of the temporary file
- a whole synchronization round: fetching a newer version, carrying out a
  deletion, the repositories that must not be merged, and the busy unit that
  lets a scheduled round wait
"""

import asyncio
import contextlib
import hashlib
import logging
import os
import socket
from datetime import datetime, timedelta
from urllib.parse import urlencode

import pytest

import unit as unit_module
from manifest import TEMP_SUFFIX, current_hash, hash_file, is_deleted
from network_messages import (
    FILE_HASH_HEADER,
    FILE_TOKEN_HEADER,
    FILE_UNIT_HEADER,
    STATUS_FAULTY,
    STATUS_OK,
    manifest_status,
    parse_file_headers,
)
from unit import (
    TransferError,
    Unit,
    _check_served_file,
    _handle_connection,
    _publish_fetched_file,
    _read_body,
    _read_response_head,
    _read_until_eof,
    _request_head,
    _stream_into_file,
)
from unit_config import DEFAULT_MAX_PARALLEL_TRANSFERS, DEFAULT_TRANSFER_TIMEOUT, UnitConfig


# ------------------------------------------------------------------- helpers

TOKEN = "shared-unit-token"
REPO = "photos"


def free_tcp_port():
    """An OS-assigned free TCP port (a high ephemeral port)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def write_file(root, rel_path, data):
    """Write one repository file, creating the parent directories."""
    path = root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    return path


def make_unit(tmp_path, identifier, content=None, repo_name=REPO, **overrides):
    """A unit with one repository under ``tmp_path``.

    Returns ``(unit, repo_root)``; the repository contains the given
    ``{relative path: bytes or text}`` mapping.  ``manifest_storage`` is kept
    per unit so that two units of one test never share a manifest.
    """
    root = tmp_path / identifier / repo_name
    root.mkdir(parents=True, exist_ok=True)
    for rel_path, data in (content or {}).items():
        write_file(root, rel_path, data)
    config = UnitConfig(
        port=free_tcp_port(),
        identifier=identifier,
        trusted_token=TOKEN,
        sync_delay=3600,
        integrity_delay=3600,
        repository=[{"name": repo_name, "path": str(root)}],
        manifest_storage=str(tmp_path / identifier / "manifests"),
        **overrides,
    )
    unit = Unit(config)
    unit.load_manifests()
    return unit, root


def fetched_config(**overrides):
    """A config carrying only the fields ``_publish_fetched_file`` reads."""
    kwargs = dict(
        port=free_tcp_port(),
        identifier="unit-a",
        trusted_token=TOKEN,
        sync_delay=3600,
        integrity_delay=3600,
        repository=[{"name": REPO, "path": "/data/unused"}],
        manifest_storage="/tmp/unused",
    )
    kwargs.update(overrides)
    return UnitConfig(**kwargs)


class FakeDiscovery:
    """The one part of :class:`unit.Discovery` synchronization uses."""

    def __init__(self, **targets):
        self.targets = dict(targets)

    def peer_target(self, unit_id):
        return self.targets.get(unit_id)


def point_at(unit, unit_id, port):
    """Make ``unit`` know that ``unit_id`` serves on the loopback ``port``."""
    unit.set_discovery(FakeDiscovery(**{unit_id: ("127.0.0.1", port)}))
    unit.register_remote(unit_id)


def reader_of(chunks):
    """A ``StreamReader`` fed with ``chunks`` and then at end of stream."""
    stream = asyncio.StreamReader()
    for chunk in chunks:
        stream.feed_data(chunk)
    stream.feed_eof()
    return stream




async def start_server(unit):
    """Serve one unit on the loopback interface: ``(server, port)``."""
    server = await asyncio.start_server(
        lambda reader, writer: _handle_connection(unit, reader, writer),
        "127.0.0.1",
        0,
    )
    return server, server.sockets[0].getsockname()[1]


async def stop_server(server):
    """Close a server started by :func:`start_server`."""
    server.close()
    await server.wait_closed()


async def start_raw_peer(chunks):
    """A stand-in peer that answers every request with ``chunks``.

    Used for the answers a well-behaved unit never sends: a truncated body or
    a head that is not HTTP.  Returns ``(server, port)``.
    """

    async def handle(reader, writer):
        with contextlib.suppress(asyncio.LimitOverrunError, ValueError):
            await reader.readuntil(b"\r\n\r\n")
        for chunk in chunks:
            writer.write(chunk)
            await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


async def start_silent_peer():
    """A peer that accepts the connection and never answers."""

    async def handle(reader, writer):
        with contextlib.suppress(asyncio.LimitOverrunError, ValueError):
            await reader.readuntil(b"\r\n\r\n")
        # the peer answers nothing and holds the connection until the requester
        # gives up and hangs up, which is when this handler is done
        with contextlib.suppress(OSError):
            await reader.read(1)
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


async def http_get(port, target, headers=None):
    """One plain ``GET`` to a unit; returns ``(status, headers, body)``."""
    lines = [f"GET {target} HTTP/1.1", "Host: 127.0.0.1", "Connection: close"]
    for name, value in (headers or {}).items():
        lines.append(f"{name}: {value}")
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(("\r\n".join(lines) + "\r\n\r\n").encode("iso-8859-1"))
        await writer.drain()
        status, _, response_headers = await _read_response_head(reader)
        if "content-length" in response_headers:
            body = await _read_body(reader, response_headers, 30)
        else:
            body = await _read_until_eof(reader, 30)
        return status, response_headers, body
    finally:
        writer.close()
        with contextlib.suppress(ConnectionError, OSError):
            await writer.wait_closed()


def file_query(rel_path, repository=REPO):
    """The ``/file`` request target for one repository-relative path."""
    return "/file?" + urlencode({"repository": repository, "path": rel_path})


async def served(port, rel_path, repository=REPO, token=TOKEN):
    """Ask a unit for one file over ``/file`` (``token=None`` sends none)."""
    headers = {} if token is None else {FILE_TOKEN_HEADER: token}
    return await http_get(port, file_query(rel_path, repository), headers)


def tamper_file(path, new_content):
    """Replace the content of a file while keeping its modification time."""
    st = path.stat()
    path.write_bytes(new_content)
    os.utime(path, (st.st_atime, st.st_mtime))


async def serving_unit(tmp_path, content=None, **overrides):
    """A unit whose repository is scanned and then served.

    Returns ``(unit, port, server)``.
    """
    unit, _ = make_unit(tmp_path, "unit-a", content, **overrides)
    await unit.integrity_check()
    server, port = await start_server(unit)
    return unit, port, server



# ------------------------------------------------------------------- serving


def test_serves_a_tracked_file(tmp_path):
    async def scenario():
        unit, port, server = await serving_unit(
            tmp_path, {"docs/note.txt": b"hello peer"}
        )
        try:
            status, headers, body = await served(port, "docs/note.txt")
        finally:
            await stop_server(server)
        path = unit.repo_path(REPO) / "docs" / "note.txt"
        assert status == 200
        assert body == b"hello peer"
        # the hash, the size and the name of the serving unit
        assert parse_file_headers(headers) == (
            hash_file(path),
            path.stat().st_size,
            "unit-a",
        )
        assert headers["content-type"] == "application/octet-stream"
        # serving a file never changes the state of the unit
        assert unit.manifest(REPO).issues == []
        assert unit.manifest(REPO).files["docs/note.txt"]["history"] == [
            "hash:" + hash_file(path)
        ]

    asyncio.run(scenario())


def test_serves_a_file_larger_than_one_chunk(tmp_path):
    content = bytes(range(256)) * 4096  # 1 MiB, more than _CHUNK_SIZE

    async def scenario():
        unit, port, server = await serving_unit(tmp_path, {"videos/big.bin": content})
        try:
            status, headers, body = await served(port, "videos/big.bin")
        finally:
            await stop_server(server)
        assert status == 200
        assert int(headers["content-length"]) == len(content)
        assert body == content

    asyncio.run(scenario())


def test_file_request_needs_the_shared_token(tmp_path):
    async def scenario():
        unit, port, server = await serving_unit(tmp_path, {"a.txt": b"alpha"})
        try:
            missing = await served(port, "a.txt", token=None)
            wrong = await served(port, "a.txt", token="another-token")
        finally:
            await stop_server(server)
        assert missing[0] == wrong[0] == 403
        assert FILE_TOKEN_HEADER in missing[2].decode()
        assert FILE_TOKEN_HEADER in wrong[2].decode()
        assert (unit.repo_path(REPO) / "a.txt").read_bytes() == b"alpha"

    asyncio.run(scenario())


def test_file_request_method_and_target_are_validated(tmp_path):
    async def scenario():
        unit, port, server = await serving_unit(tmp_path, {"a.txt": b"alpha"})
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(
                b"POST /file?repository=photos&path=a.txt HTTP/1.1\r\n"
                + FILE_TOKEN_HEADER.encode()
                + b": "
                + TOKEN.encode()
                + b"\r\nConnection: close\r\n\r\n"
            )
            await writer.drain()
            post_status, _, post_head = await _read_response_head(reader)
            post_body = await _read_body(reader, post_head, 30)
            writer.close()
            await writer.wait_closed()
            no_path = await http_get(
                port, "/file?repository=photos", {FILE_TOKEN_HEADER: TOKEN}
            )
            ambiguous = await http_get(
                port,
                "/file?repository=photos&repository=videos&path=a.txt",
                {FILE_TOKEN_HEADER: TOKEN},
            )
            escaping = await served(port, "../outside.txt")
            windows = await served(port, "vacation\\img.jpg")
            absolute = await served(port, "/etc/passwd")
        finally:
            await stop_server(server)
        assert post_status == 405
        assert b"method" in post_body
        assert no_path[0] == 400  # 'path' missing
        assert ambiguous[0] == 400  # 'repository' given twice
        assert escaping[0] == 400  # a '..' component is not canonical
        assert windows[0] == 400  # a backslash is not a separator here
        assert absolute[0] == 400  # the path must be relative

    asyncio.run(scenario())


def test_untracked_path_and_unknown_repository_are_not_found(tmp_path):
    async def scenario():
        unit, port, server = await serving_unit(tmp_path, {"a.txt": b"alpha"})
        try:
            untracked = await served(port, "sub/other.txt")
            unknown_repo = await served(port, "a.txt", repository="videos")
        finally:
            await stop_server(server)
        assert untracked[0] == 404
        assert b"not tracked" in untracked[2]
        assert unknown_repo[0] == 404
        assert b"unknown repository" in unknown_repo[2]

    asyncio.run(scenario())


def test_deleted_file_is_gone(tmp_path):
    async def scenario():
        unit, port, server = await serving_unit(tmp_path, {"a.txt": b"alpha"})
        (unit.repo_path(REPO) / "a.txt").unlink()
        await unit.integrity_check()  # records the deletion token
        try:
            status, _, body = await served(port, "a.txt")
        finally:
            await stop_server(server)
        assert status == 410
        assert b"deleted" in body

    asyncio.run(scenario())


def test_faulty_repository_refuses_to_serve(tmp_path):
    async def scenario():
        unit, port, server = await serving_unit(tmp_path, {"a.txt": b"alpha"})
        tamper_file(unit.repo_path(REPO) / "a.txt", b"tampered!")
        await unit.integrity_check()  # the hash moved without the mtime
        assert manifest_status(unit.manifest(REPO)) == STATUS_FAULTY
        try:
            status, _, body = await served(port, "a.txt")
        finally:
            await stop_server(server)
        assert status == 409
        assert b"faulty" in body

    asyncio.run(scenario())


def test_file_that_left_its_manifest_record_is_refused(tmp_path):
    async def scenario():
        unit, port, server = await serving_unit(tmp_path, {"a.txt": b"alpha"})
        root = unit.repo_path(REPO)
        manifest = unit.manifest(REPO)
        try:
            # the content moved without a new modification time
            tamper_file(root / "a.txt", b"changed")
            hash_status, _, hash_body = await served(port, "a.txt")
            # the file is gone, while the manifest still promises it
            (root / "a.txt").unlink()
            gone_status, _, gone_body = await served(port, "a.txt")
            # a newer modification time than the manifest knows about
            write_file(root, "b.txt", b"beta")
            manifest.files["b.txt"] = {
                "history": ["hash:" + hash_file(root / "b.txt")],
                "timestamp": datetime.now() - timedelta(days=1),
            }
            moved_status, _, moved_body = await served(port, "b.txt")
        finally:
            await stop_server(server)
        assert hash_status == 409
        assert b"does not match the hash" in hash_body
        assert gone_status == 409
        assert b"disappeared" in gone_body
        assert moved_status == 409
        assert b"modified since the last integrity check" in moved_body

    asyncio.run(scenario())


def test_file_above_max_transfer_size_is_refused(tmp_path):
    async def scenario():
        unit, port, server = await serving_unit(
            tmp_path, {"a.txt": b"alpha"}, max_transfer_size=4
        )
        try:
            status, _, body = await served(port, "a.txt")
        finally:
            await stop_server(server)
        assert status == 413
        assert b"max_transfer_size=4" in body

    asyncio.run(scenario())


def test_tracked_path_that_is_no_longer_a_regular_file(tmp_path):
    async def scenario():
        unit, port, server = await serving_unit(tmp_path, {"a.txt": b"alpha"})
        root = unit.repo_path(REPO)
        # the same content under another name, so that only the file type of
        # the tracked path differs from what the manifest records
        elsewhere = write_file(root.parent, "elsewhere.txt", b"alpha")
        stamp = unit.manifest(REPO).files["a.txt"]["timestamp"].timestamp()
        os.utime(elsewhere, (stamp, stamp))
        (root / "a.txt").unlink()
        (root / "a.txt").symlink_to(elsewhere)
        try:
            status, _, body = await served(port, "a.txt")
        finally:
            await stop_server(server)
        assert status == 415
        assert b"not a regular file" in body

    asyncio.run(scenario())


def test_check_served_file_reports_the_size_or_a_refusal(tmp_path):
    path = write_file(tmp_path / "repo", "a.txt", b"alpha")
    digest = hash_file(path)
    modified = datetime.fromtimestamp(path.stat().st_mtime)
    assert _check_served_file(path, digest, modified, None) == (
        path.stat().st_size,
        None,
    )

    size, refusal = _check_served_file(path, digest, modified, 4)
    assert size is None
    assert refusal[0] == 413
    assert "max_transfer_size=4" in refusal[1]

    size, refusal = _check_served_file(path, "0" * 32, modified, None)
    assert size is None
    assert refusal[0] == 409
    assert "does not match the hash" in refusal[1]

    size, refusal = _check_served_file(
        path.parent / "missing.txt", digest, modified, None
    )
    assert size is None
    assert refusal[0] == 409
    assert "disappeared" in refusal[1]

    older = datetime.now() - timedelta(days=1)
    size, refusal = _check_served_file(path, digest, older, None)
    assert size is None
    assert refusal[0] == 409
    assert "modified since the last integrity check" in refusal[1]


# ------------------------------------------------------------ client helpers


def test_request_head_carries_the_token_only_for_files():
    file_head = _request_head("/file?repository=photos&path=a.txt", TOKEN).decode()
    assert file_head.startswith("GET /file?repository=photos&path=a.txt HTTP/1.1\r\n")
    assert f"{FILE_TOKEN_HEADER}: {TOKEN}\r\n" in file_head
    assert "Connection: close\r\n" in file_head
    assert file_head.endswith("\r\n")

    status_head = _request_head("/status?unit=unit-a").decode()
    assert FILE_TOKEN_HEADER not in status_head  # /status is not authenticated
    assert status_head.startswith("GET /status?unit=unit-a HTTP/1.1\r\n")

    assert _request_head("/status").decode().startswith("GET /status HTTP/1.1")


def test_read_response_head_parses_the_answer_of_a_peer():
    async def scenario():
        reader = reader_of(
            [
                b"HTTP/1.1 200 OK\r\n",
                b"Content-Length: 5\r\n",
                b"X-Beacon-File-Hash: 0123456789abcdef0123456789abcdef\r\n",
                b"\r\n",
                b"the body follows",
            ]
        )
        status, reason, headers = await _read_response_head(reader)
        assert status == 200
        assert reason == "OK"
        # header names are lower-case, the last value of a repeated name wins
        assert headers["content-length"] == "5"
        assert headers[FILE_HASH_HEADER.lower()] == "0123456789abcdef0123456789abcdef"
        assert await reader.read(16) == b"the body follows"

    asyncio.run(scenario())


def test_read_response_head_rejects_an_answer_that_is_not_http():
    async def scenario():
        with pytest.raises(ValueError, match="not an HTTP response status line"):
            await _read_response_head(reader_of([b"not an HTTP answer\r\n\r\n"]))
        with pytest.raises(ValueError, match="status line"):
            await _read_response_head(reader_of([b"HTTP/1.1\r\n\r\n"]))
        with pytest.raises(ValueError, match="malformed HTTP status code"):
            await _read_response_head(reader_of([b"HTTP/1.1 abc OK\r\n\r\n"]))
        # the head alone must stay below _MAX_HEAD
        with pytest.raises(ValueError, match="no response head"):
            await _read_response_head(reader_of([b"X" * (128 * 1024)]))
        # a peer that goes away before answering is a refusal, not a crash
        with pytest.raises(ValueError, match="closed the connection"):
            await _read_response_head(reader_of([]))

    asyncio.run(scenario())


def test_read_body_returns_the_announced_number_of_bytes():
    async def scenario():
        reader = reader_of([b"hello", b" world", b"and more"])
        body = await _read_body(reader, {"content-length": "11"}, 30)
        assert body == b"hello world"
        # only what was announced is taken, the rest is left in the stream
        assert await reader.read(8) == b"and more"

    asyncio.run(scenario())


def test_read_body_refuses_a_body_that_stays_short():
    async def scenario():
        with pytest.raises(ValueError, match="4 of the 11 announced bytes"):
            await _read_body(reader_of([b"hell"]), {"content-length": "11"}, 10)
        # an unusable Content-Length is refused instead of guessed at
        with pytest.raises(ValueError, match="malformed Content-Length"):
            await _read_body(reader_of([b"abc"]), {"content-length": "twelve"}, 10)
        # without an announced length the body runs to the end of the stream
        assert await _read_body(reader_of([b"tail"]), {}, 10) == b"tail"
        # an empty body needs no reading at all
        assert await _read_body(reader_of([]), {"content-length": "0"}, 10) == b""

    asyncio.run(scenario())


def test_read_until_eof_stops_at_the_end_of_the_stream():
    async def scenario():
        assert await _read_until_eof(reader_of([b"ping"]), 10) == b"ping"
        assert await _read_until_eof(reader_of([]), 10) == b""

    asyncio.run(scenario())


def test_stream_into_file_returns_the_bytes_and_the_digest(tmp_path):
    async def scenario():
        part_path = tmp_path / ("target.txt" + TEMP_SUFFIX)
        content = b"a" * 5000 + b"b" * 4000
        chunks = [content[i : i + 700] for i in range(0, len(content), 700)]
        written, digest = await _stream_into_file(
            reader_of(chunks), part_path, len(content), 30
        )
        assert written == len(content)
        assert digest.hexdigest() == hashlib.md5(content).hexdigest()
        assert part_path.read_bytes() == content

    asyncio.run(scenario())


def test_stream_into_file_stops_at_the_allowed_size(tmp_path):
    async def scenario():
        # a peer that ignores its own max_transfer_size must not fill the disk
        with pytest.raises(ValueError, match="more than the 100 bytes"):
            await _stream_into_file(
                reader_of([b"x" * 500]), tmp_path / "part", 500, 30, 100
            )
        # a peer that disappears mid-transfer leaves a short file behind; the
        # caller compares the number of bytes with the announced size
        written, digest = await _stream_into_file(
            reader_of([b"abc"]), tmp_path / "part2", 90, 30
        )
        assert written == 3

    asyncio.run(scenario())


def test_publish_fetched_file_places_it_atomically(tmp_path):
    config = fetched_config(fetched_file_mode=0o600)
    target = tmp_path / "repo" / "docs" / "note.txt"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"the old version")
    part_path = tmp_path / "repo" / "docs" / ("note.txt" + TEMP_SUFFIX)
    part_path.write_bytes(b"the new version")

    _publish_fetched_file(config, part_path, target)

    assert target.read_bytes() == b"the new version"
    assert not part_path.exists()
    assert target.stat().st_mode & 0o777 == 0o600


def test_publish_fetched_file_keeps_the_mode_when_none_is_configured(tmp_path):
    config = fetched_config()  # fetched_file_mode left out of the config
    part_path = tmp_path / ("plain.txt" + TEMP_SUFFIX)
    part_path.write_bytes(b"content")
    os.chmod(part_path, 0o666)
    target = tmp_path / "plain.txt"

    _publish_fetched_file(config, part_path, target)

    assert config.fetched_file_mode is None
    assert target.read_bytes() == b"content"
    assert target.stat().st_mode & 0o777 == 0o666


def test_publish_fetched_file_leaves_nothing_behind_when_it_fails(tmp_path):
    config = fetched_config()
    part_path = tmp_path / "part.txt"
    part_path.write_bytes(b"content")
    # a target whose parent does not exist cannot take the file
    with pytest.raises(TransferError, match="cannot place"):
        _publish_fetched_file(config, part_path, tmp_path / "missing" / "file.txt")
    assert not part_path.exists()






# ---------------------------------------------------------------- fetching files


def digest_of(content):
    """The hash of ``content`` as a manifest and a peer record it."""
    return hashlib.md5(content).hexdigest()


def part_of(path):
    """The temporary path a fetch of ``path`` is downloaded into."""
    return path.with_name(path.name + TEMP_SUFFIX)


def test_a_fetched_file_arrives_verified_and_under_its_own_name(tmp_path):
    async def scenario():
        content = b"the version only the peer has"
        _peer, port, server = await serving_unit(tmp_path, {"docs/note.txt": content})
        fetcher, root = make_unit(tmp_path, "unit-b")
        point_at(fetcher, "unit-a", port)
        try:
            await fetcher._fetch_file("unit-a", REPO, "docs/note.txt", digest_of(content))
        finally:
            await stop_server(server)
        target = root / "docs" / "note.txt"
        assert target.read_bytes() == content
        # the parent directory was created and nothing of the transfer is left
        assert sorted(path.name for path in (root / "docs").iterdir()) == ["note.txt"]

    asyncio.run(scenario())


def test_a_file_the_peer_sends_under_another_hash_is_not_kept(tmp_path):
    async def scenario():
        # the peer moved on since the manifest that asked for this fetch was read
        _peer, port, server = await serving_unit(tmp_path, {"a.txt": b"what the peer holds"})
        fetcher, root = make_unit(tmp_path, "unit-b")
        point_at(fetcher, "unit-a", port)
        try:
            with pytest.raises(TransferError, match="serves hash"):
                await fetcher._fetch_file(
                    "unit-a", REPO, "a.txt", digest_of(b"an older version")
                )
        finally:
            await stop_server(server)
        assert not (root / "a.txt").exists()
        assert not part_of(root / "a.txt").exists()

    asyncio.run(scenario())


def test_a_refused_file_is_reported_with_the_reason_of_the_peer(tmp_path):
    async def scenario():
        _peer, port, server = await serving_unit(tmp_path, {"a.txt": b"here I am"})
        fetcher, root = make_unit(tmp_path, "unit-b")
        point_at(fetcher, "unit-a", port)
        try:
            with pytest.raises(TransferError, match="refused photos/not-here.txt: 404"):
                await fetcher._fetch_file("unit-a", REPO, "not-here.txt", digest_of(b"x"))
        finally:
            await stop_server(server)
        assert not (root / "not-here.txt").exists()

    asyncio.run(scenario())


def test_a_peer_without_a_known_address_cannot_send_a_file(tmp_path):
    async def scenario():
        fetcher, _ = make_unit(tmp_path, "unit-b")
        with pytest.raises(TransferError, match="has no known address"):
            await fetcher._fetch_file("unit-z", REPO, "a.txt", digest_of(b"x"))

    asyncio.run(scenario())


def test_a_file_above_the_own_max_transfer_size_is_not_kept(tmp_path):
    async def scenario():
        content = b"x" * 300
        _peer, port, server = await serving_unit(tmp_path, {"big.bin": content})
        fetcher, root = make_unit(tmp_path, "unit-b", max_transfer_size=100)
        point_at(fetcher, "unit-a", port)
        try:
            with pytest.raises(TransferError, match="more than the 100 bytes"):
                await fetcher._fetch_file("unit-a", REPO, "big.bin", digest_of(content))
        finally:
            await stop_server(server)
        assert not (root / "big.bin").exists()
        assert not part_of(root / "big.bin").exists()

    asyncio.run(scenario())



def test_a_transfer_that_stops_half_way_is_not_published(tmp_path):
    async def scenario():
        announced = b"the full body of the file"
        head = (
            "HTTP/1.1 200 OK\r\n"
            f"{FILE_UNIT_HEADER}: unit-a\r\n"
            f"{FILE_HASH_HEADER}: {digest_of(announced)}\r\n"
            f"Content-Length: {len(announced)}\r\n\r\n"
        ).encode("iso-8859-1")
        server, port = await start_raw_peer([head + announced[:6]])
        fetcher, root = make_unit(tmp_path, "unit-b")
        point_at(fetcher, "unit-a", port)
        try:
            with pytest.raises(TransferError, match="sent 6 of the 25 bytes"):
                await fetcher._fetch_file("unit-a", REPO, "a.txt", digest_of(announced))
        finally:
            await stop_server(server)
        assert not (root / "a.txt").exists()
        assert not part_of(root / "a.txt").exists()

    asyncio.run(scenario())


def test_an_answer_that_is_not_http_is_reported_as_a_failed_fetch(tmp_path):
    async def scenario():
        server, port = await start_raw_peer([b"a peer speaking another protocol\r\n\r\n"])
        fetcher, root = make_unit(tmp_path, "unit-b")
        point_at(fetcher, "unit-a", port)
        try:
            with pytest.raises(TransferError, match=r"cannot fetch 'a\.txt'"):
                await fetcher._fetch_file("unit-a", REPO, "a.txt", digest_of(b"x"))
        finally:
            await stop_server(server)
        assert not part_of(root / "a.txt").exists()

    asyncio.run(scenario())


def test_a_peer_that_never_answers_is_given_up_on(tmp_path):
    async def scenario():
        server, port = await start_silent_peer()
        fetcher, root = make_unit(tmp_path, "unit-b", transfer_timeout=1)
        point_at(fetcher, "unit-a", port)
        try:
            with pytest.raises(TransferError, match="stayed idle for 1 s"):
                await fetcher._fetch_file("unit-a", REPO, "a.txt", digest_of(b"x"))
        finally:
            await stop_server(server)
        assert not part_of(root / "a.txt").exists()

    asyncio.run(scenario())


def test_a_part_file_of_an_earlier_attempt_is_left_alone(tmp_path):
    async def scenario():
        fetcher, root = make_unit(tmp_path, "unit-b")
        point_at(fetcher, "unit-a", free_tcp_port())
        leftover = part_of(root / "a.txt")
        leftover.write_bytes(b"half a file of an earlier round")
        with pytest.raises(TransferError, match="is already being downloaded"):
            await fetcher._fetch_file("unit-a", REPO, "a.txt", digest_of(b"x"))
        # nothing was started, so what lies next to the target stays as it is
        assert leftover.read_bytes() == b"half a file of an earlier round"

    asyncio.run(scenario())



# ------------------------------------------------------- a synchronization round


def test_a_round_fetches_what_only_the_peer_brought_up_to_date(tmp_path):
    async def scenario():
        peer, peer_root = make_unit(
            tmp_path, "unit-a", {"docs/note.txt": "the first version"}
        )
        await peer.integrity_check()
        fetcher, root = make_unit(
            tmp_path, "unit-b", {"docs/note.txt": "the first version"}
        )
        await fetcher.integrity_check()
        # only the peer learns about the newer and about the brand new file
        write_file(peer_root, "docs/note.txt", "the newer version")
        write_file(peer_root, "docs/new.txt", "the peer alone has this")
        await peer.integrity_check()

        server, port = await start_server(peer)
        point_at(fetcher, "unit-a", port)
        try:
            summary = await fetcher.sync(reason="test")
        finally:
            await stop_server(server)

        assert summary == {
            "reason": "test",
            "peers": 1,
            "fetches": 2,
            "deletions": 0,
            "failures": 0,
        }
        assert (root / "docs" / "note.txt").read_bytes() == b"the newer version"
        assert (root / "docs" / "new.txt").read_bytes() == b"the peer alone has this"
        files = fetcher.manifest(REPO).files
        assert current_hash(files["docs/note.txt"]) == digest_of(b"the newer version")
        assert current_hash(files["docs/new.txt"]) == digest_of(b"the peer alone has this")
        name, status, last_sync = fetcher.remotes[0]
        assert name == "unit-a"
        assert status == STATUS_OK
        assert last_sync is not None

    asyncio.run(scenario())


def test_a_round_carries_out_a_deletion_the_peer_already_made(tmp_path):
    async def scenario():
        peer, peer_root = make_unit(tmp_path, "unit-a", {"docs/old.txt": "to be removed"})
        await peer.integrity_check()
        fetcher, root = make_unit(tmp_path, "unit-b", {"docs/old.txt": "to be removed"})
        await fetcher.integrity_check()
        (peer_root / "docs" / "old.txt").unlink()
        await peer.integrity_check()

        server, port = await start_server(peer)
        point_at(fetcher, "unit-a", port)
        try:
            summary = await fetcher.sync(reason="test")
        finally:
            await stop_server(server)

        assert summary["fetches"] == 0
        assert summary["deletions"] == 1
        assert summary["failures"] == 0
        assert not (root / "docs" / "old.txt").exists()
        # the merged history records the deletion instead of forgetting the file
        assert is_deleted(fetcher.manifest(REPO).files["docs/old.txt"])

    asyncio.run(scenario())


def test_two_rounds_leave_both_units_with_the_same_content(tmp_path):
    async def scenario():
        first = {"a.txt": "the first version", "b.txt": "also the first version"}
        peer, peer_root = make_unit(tmp_path, "unit-a", first)
        await peer.integrity_check()
        other, other_root = make_unit(tmp_path, "unit-b", first)
        await other.integrity_check()

        # one unit moves ahead: a new version, a deletion and a new file
        write_file(peer_root, "a.txt", "the second version")
        (peer_root / "b.txt").unlink()
        write_file(peer_root, "c.txt", "a file only the peer has")
        await peer.integrity_check()

        server, port = await start_server(peer)
        point_at(other, "unit-a", port)
        try:
            summary = await other.sync(reason="test")
        finally:
            await stop_server(server)
        assert (summary["fetches"], summary["deletions"], summary["failures"]) == (2, 1, 0)

        # the second round runs into a peer that has nothing left to learn
        server, port = await start_server(other)
        point_at(peer, "unit-b", port)
        try:
            summary = await peer.sync(reason="test")
        finally:
            await stop_server(server)
        assert (summary["fetches"], summary["deletions"], summary["failures"]) == (0, 0, 0)

        assert sorted(path.name for path in peer_root.iterdir()) == ["a.txt", "c.txt"]
        assert sorted(path.name for path in other_root.iterdir()) == ["a.txt", "c.txt"]
        for name in ("a.txt", "c.txt"):
            assert current_hash(peer.manifest(REPO).files[name]) == current_hash(
                other.manifest(REPO).files[name]
            )
        assert is_deleted(peer.manifest(REPO).files["b.txt"])
        assert is_deleted(other.manifest(REPO).files["b.txt"])

    asyncio.run(scenario())



def test_a_repository_the_peer_reports_as_faulty_is_left_alone(tmp_path, caplog):
    async def scenario():
        peer, peer_root = make_unit(tmp_path, "unit-a", {"a.txt": "the newer version"})
        await peer.integrity_check()
        # the peer cannot vouch for its own data: its manifest says so
        peer.manifest(REPO).add_issue("fault", "a.txt", description="unreadable sector")
        fetcher, root = make_unit(tmp_path, "unit-b", {"a.txt": "our own version"})
        await fetcher.integrity_check()

        server, port = await start_server(peer)
        point_at(fetcher, "unit-a", port)
        with caplog.at_level(logging.INFO, logger="beacon.unit"):
            try:
                summary = await fetcher.sync(reason="test")
            finally:
                await stop_server(server)

        assert (summary["fetches"], summary["deletions"], summary["failures"]) == (0, 0, 0)
        assert (root / "a.txt").read_bytes() == b"our own version"
        assert "reports it as faulty" in caplog.text
        # reading the peer is still allowed: it was met and reported
        assert fetcher.remotes[0][1] == STATUS_FAULTY

    asyncio.run(scenario())


def test_a_repository_that_is_faulty_here_is_left_alone(tmp_path, caplog):
    async def scenario():
        peer, peer_root = make_unit(tmp_path, "unit-a", {"a.txt": "the newer version"})
        await peer.integrity_check()
        fetcher, root = make_unit(tmp_path, "unit-b", {"a.txt": "our own version"})
        await fetcher.integrity_check()
        fetcher.manifest(REPO).add_issue("fault", "a.txt", description="cannot read")

        server, port = await start_server(peer)
        point_at(fetcher, "unit-a", port)
        with caplog.at_level(logging.INFO, logger="beacon.unit"):
            try:
                summary = await fetcher.sync(reason="test")
            finally:
                await stop_server(server)

        assert (summary["fetches"], summary["deletions"]) == (0, 0)
        assert (root / "a.txt").read_bytes() == b"our own version"
        assert "it is faulty on this unit" in caplog.text

    asyncio.run(scenario())


def test_a_repository_the_peer_does_not_share_is_skipped(tmp_path):
    async def scenario():
        peer, _ = make_unit(tmp_path, "unit-a", {"clip.mp4": "not ours"}, repo_name="films")
        await peer.integrity_check()
        fetcher, root = make_unit(tmp_path, "unit-b", {"a.txt": "ours"})
        await fetcher.integrity_check()

        server, port = await start_server(peer)
        point_at(fetcher, "unit-a", port)
        try:
            summary = await fetcher.sync(reason="test")
        finally:
            await stop_server(server)

        assert (summary["peers"], summary["fetches"], summary["failures"]) == (1, 0, 0)
        assert sorted(path.name for path in root.iterdir()) == ["a.txt"]

    asyncio.run(scenario())


def test_a_peer_that_cannot_be_reached_is_reported_and_left(tmp_path, caplog):
    async def scenario():
        fetcher, root = make_unit(tmp_path, "unit-b", {"a.txt": "ours"})
        await fetcher.integrity_check()
        fetcher.set_discovery(FakeDiscovery())  # knows no address at all
        fetcher.register_remote("unit-z")

        with caplog.at_level(logging.WARNING, logger="beacon.unit"):
            summary = await fetcher.sync(reason="test")

        assert summary == {
            "reason": "test",
            "peers": 1,
            "fetches": 0,
            "deletions": 0,
            "failures": 0,
        }
        assert (root / "a.txt").read_bytes() == b"ours"
        # the peer was never met, so nothing is recorded about the round
        assert fetcher.remotes == [("unit-z", STATUS_OK, None)]
        assert "unit 'unit-z' has no known address" in caplog.text

    asyncio.run(scenario())


def test_a_busy_unit_keeps_the_round_for_later(tmp_path, caplog):
    async def scenario():
        fetcher, _ = make_unit(tmp_path, "unit-b", {"a.txt": "ours"})
        with caplog.at_level(logging.INFO, logger="beacon.unit"):
            fetcher._sync_in_progress = True
            assert await fetcher.sync(reason="scheduled") is None
            assert "a round is already running" in caplog.text
            fetcher._sync_in_progress = False
            fetcher._check_in_progress = True
            assert await fetcher.sync(reason="scheduled") is None
            assert "an integrity check is running" in caplog.text
        assert fetcher.is_busy

    asyncio.run(scenario())


def test_the_transfer_limits_fall_back_to_the_documented_defaults(tmp_path):
    plain, _ = make_unit(tmp_path, "unit-a")
    assert plain.transfer_timeout == DEFAULT_TRANSFER_TIMEOUT
    assert plain.max_parallel_transfers == DEFAULT_MAX_PARALLEL_TRANSFERS
    assert plain._config.max_transfer_size is None

    limited, _ = make_unit(
        tmp_path,
        "unit-b",
        transfer_timeout=7,
        max_parallel_transfers=5,
        max_transfer_size=1024,
    )
    assert limited.transfer_timeout == 7
    assert limited.max_parallel_transfers == 5
    assert limited._config.max_transfer_size == 1024

