"""Tests for the Beacon unit script (``scripts/unit.py``).

Covers the single-unit configuration:

- 5-field cron schedule evaluation (``next_cron_match``)
- the scheduled task loop (``schedule_loop``)
- unit state: manifest loading/creation, integrity checks, re-evaluation
  and the no-op synchronization
- the HTTP endpoints (``/``, ``/status``, ``/network``, ``/summary``,
  ``/reevaluate``) and the CLI entry point (``main``)
"""

import asyncio
import contextlib
import logging
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

import pytest
import yaml

from manifest import Manifest, current_hash, hash_file
from network_messages import (
    STATUS_FAULTY,
    STATUS_OK,
    parse_network_status,
    parse_status,
)
from unit import Unit, _handle_connection, main, next_cron_match, schedule_loop
from unit_config import UnitConfig
from test_unit_config import valid_fields


# ------------------------------------------------------------------ helpers


def make_unit(tmp_path, name="photos", content=None, **config_overrides):
    """A unit with one repository under ``tmp_path``.

    Returns ``(unit, repo_root)``; the repository contains the given
    ``{relative path: text}`` mapping.
    """
    repo_root = tmp_path / "repo" / name
    repo_root.mkdir(parents=True, exist_ok=True)
    if content is not None:
        for rel_path, data in content.items():
            path = repo_root / rel_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(data, encoding="utf-8")
    config = UnitConfig(
        port=8090,
        identifier="unit-1",
        trusted_token="token",
        sync_delay=3600,
        integrity_delay=3600,
        repository=[{"name": name, "path": str(repo_root)}],
        manifest_storage=str(tmp_path / "manifests"),
        **config_overrides,
    )
    unit = Unit(config)
    unit.load_manifests()
    return unit, repo_root


def tamper_file(path, new_content):
    """Change the file content but restore the original mtime.

    Produces the "hash changed without a modification time change"
    situation that the integrity check records as a fault.
    """
    st = path.stat()
    path.write_text(new_content, encoding="utf-8")
    os.utime(path, (st.st_atime, st.st_mtime))


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _http_request(url, data=None):
    """Minimal synchronous HTTP GET/POST.

    Returns ``(status, headers, body text)``.
    """
    request = urllib.request.Request(
        url, data=data, method="POST" if data is not None else "GET"
    )
    try:
        response = urllib.request.urlopen(request, timeout=5)
    except urllib.error.HTTPError as error:
        response = error
    body = response.read().decode("utf-8")
    return response.status, response.headers, body


async def _http(url, data=None):
    """:func:`_http_request` in a worker thread so the event loop (and the
    unit's HTTP server) stays responsive."""
    return await asyncio.to_thread(_http_request, url, data)


def with_server(unit, scenario):
    """Run ``scenario(base_url)`` while the unit serves on a free port.

    Everything (scans, requests, assertions against live state) happens
    inside one event loop, as in production.
    """
    port = _free_port()

    async def run():
        server = await asyncio.start_server(
            lambda reader, writer: _handle_connection(unit, reader, writer),
            "127.0.0.1",
            port,
        )
        try:
            return await scenario(f"http://127.0.0.1:{port}")
        finally:
            server.close()
            await server.wait_closed()

    return asyncio.run(run())


# ------------------------------------------------------------- cron matching


def test_next_cron_match_every_minute():
    assert next_cron_match(
        "* * * * *", datetime(2026, 11, 6, 12, 0, 30)
    ) == datetime(2026, 11, 6, 12, 1)


def test_next_cron_match_daily_at_time():
    assert next_cron_match(
        "0 12 * * *", datetime(2026, 11, 6, 11, 59)
    ) == datetime(2026, 11, 6, 12, 0)
    # strictly after: an exact match at the current minute is missed
    assert next_cron_match(
        "0 12 * * *", datetime(2026, 11, 6, 12, 0)
    ) == datetime(2026, 11, 7, 12, 0)


def test_next_cron_match_weekday():
    # 2026-11-06 is a Friday, 2026-11-07 a Saturday
    assert next_cron_match(
        "45 23 * * 6", datetime(2026, 11, 6, 0, 0)
    ) == datetime(2026, 11, 7, 23, 45)


def test_next_cron_match_day_and_weekday_or():
    # day of month 13 OR Friday
    assert next_cron_match(
        "0 0 13 * 5", datetime(2026, 11, 5, 12, 0)
    ) == datetime(2026, 11, 6, 0, 0)
    assert next_cron_match(
        "0 0 13 * 5", datetime(2026, 11, 6, 0, 0)
    ) == datetime(2026, 11, 13, 0, 0)


def test_next_cron_match_specific_month():
    assert next_cron_match(
        "0 0 1 6 *", datetime(2026, 11, 6, 0, 0)
    ) == datetime(2027, 6, 1, 0, 0)


def test_next_cron_match_impossible_schedule():
    with pytest.raises(ValueError):
        next_cron_match("0 0 31 4 *", datetime(2026, 1, 1))


def test_next_cron_match_rejects_bad_schedule():
    with pytest.raises(ValueError):
        next_cron_match("60 0 * * *", datetime(2026, 1, 1))
    with pytest.raises(ValueError):
        next_cron_match("* * * *", datetime(2026, 1, 1))


def test_next_cron_match_rejects_bad_after():
    with pytest.raises(ValueError):
        next_cron_match("* * * * *", "2026-01-01")


# ------------------------------------------------------------ schedule loop


async def _noop():
    pass


def test_schedule_loop_requires_exactly_one():
    async def scenario():
        with pytest.raises(ValueError):
            await schedule_loop(_noop)
        with pytest.raises(ValueError):
            await schedule_loop(_noop, delay=5, schedule="* * * * *")

    asyncio.run(scenario())


def test_schedule_loop_runs_on_delay():
    runs = []

    async def action():
        runs.append(1)

    async def scenario():
        task = asyncio.create_task(schedule_loop(action, delay=0.02))
        await asyncio.sleep(0.11)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert len(runs) >= 3  # immediate run plus several intervals
    assert len(runs) <= 10



# ---------------------------------------------------------------- unit state


def test_load_manifests_creates_missing(tmp_path):
    unit, repo_root = make_unit(tmp_path, content={"a.txt": "alpha"})
    assert unit.known_repositories() == ["photos"]
    manifest = unit.manifest("photos")
    assert manifest.files == {}
    assert not unit.manifest_path("photos").exists()
    assert str(repo_root) in str(manifest.path)
    assert unit.remotes == []
    assert unit.overall_status() == STATUS_OK


def test_load_manifests_reloads_existing(tmp_path):
    unit, _ = make_unit(tmp_path, content={"a.txt": "alpha"})
    asyncio.run(unit.integrity_check())
    unit2 = Unit(unit.config)
    unit2.load_manifests()
    assert unit2.manifest("photos").files == unit.manifest("photos").files
    assert unit2.manifest("photos").unit_id == unit.identifier


def test_unknown_repository(tmp_path):
    unit, _ = make_unit(tmp_path)
    with pytest.raises(KeyError):
        unit.manifest("missing")
    with pytest.raises(KeyError):
        unit.repo_path("missing")


def test_overall_status(tmp_path):
    unit, _ = make_unit(tmp_path, content={"a.txt": "alpha"})
    assert unit.overall_status() == STATUS_OK
    unit.manifest("photos").add_issue("fault", "a.txt", description="boom")
    assert unit.overall_status() == STATUS_FAULTY


def test_sync_is_noop_for_single_unit(tmp_path, caplog):
    unit, _ = make_unit(tmp_path, content={"a.txt": "alpha"})
    with caplog.at_level(logging.INFO, logger="beacon.unit"):
        asyncio.run(unit.sync())
    assert "no known peers" in caplog.text


# ------------------------------------------------------------ integrity check


def test_integrity_check_creates_manifest(tmp_path):
    unit, _ = make_unit(
        tmp_path, content={"a.txt": "alpha", "sub/b.txt": "beta"}
    )
    asyncio.run(unit.integrity_check())
    manifest_file = unit.manifest_path("photos")
    assert manifest_file.exists()
    loaded = Manifest.load(manifest_file)
    assert set(loaded.files) == {"a.txt", "sub/b.txt"}
    assert current_hash(loaded.files["a.txt"]) == hash_file(
        tmp_path / "repo" / "photos" / "a.txt"
    )
    assert not loaded.is_faulty()


def test_integrity_check_tracks_change(tmp_path):
    unit, repo_root = make_unit(tmp_path, content={"a.txt": "alpha"})

    async def scenario():
        await unit.integrity_check()
        (repo_root / "a.txt").write_text("alpha-modified", encoding="utf-8")
        (repo_root / "new.txt").write_text("fresh", encoding="utf-8")
        await unit.integrity_check()

    asyncio.run(scenario())
    manifest = unit.manifest("photos")
    assert len(manifest.files["a.txt"]["history"]) == 2
    assert "new.txt" in manifest.files
    assert not manifest.is_faulty()


def test_integrity_check_hash_change_without_mtime(tmp_path):
    unit, repo_root = make_unit(tmp_path, content={"a.txt": "alpha"})

    async def scenario():
        await unit.integrity_check()
        tamper_file(repo_root / "a.txt", "tampered")
        await unit.integrity_check()

    asyncio.run(scenario())
    manifest = unit.manifest("photos")
    assert manifest.is_faulty()
    assert len(manifest.files["a.txt"]["history"]) == 1
    assert unit.overall_status() == STATUS_FAULTY


def test_integrity_check_records_deletion(tmp_path):
    unit, repo_root = make_unit(tmp_path, content={"a.txt": "alpha"})

    async def scenario():
        await unit.integrity_check()
        (repo_root / "a.txt").unlink()
        await unit.integrity_check()

    asyncio.run(scenario())
    metadata = unit.manifest("photos").files["a.txt"]
    assert metadata["history"][-1] == f"deleted:{unit.identifier}"


def test_integrity_check_missing_directory(tmp_path, caplog):
    unit, repo_root = make_unit(tmp_path, content={"a.txt": "alpha"})

    async def scenario():
        await unit.integrity_check()
        (repo_root / "a.txt").unlink()
        repo_root.rmdir()
        await unit.integrity_check()

    with caplog.at_level(logging.ERROR, logger="beacon.unit"):
        asyncio.run(scenario())
    assert "does not exist" in caplog.text
    # the earlier scan result is kept
    assert "a.txt" in unit.manifest("photos").files



# --------------------------------------------------------------- reevaluate


def test_reevaluate_keeps_unresolved_fault(tmp_path):
    unit, repo_root = make_unit(tmp_path, content={"a.txt": "alpha"})

    async def scenario():
        await unit.integrity_check()
        tamper_file(repo_root / "a.txt", "tampered")
        await unit.integrity_check()
        assert unit.manifest("photos").is_faulty()
        return await unit.reevaluate_repository("photos")

    resolved = asyncio.run(scenario())
    assert resolved == 0
    assert unit.manifest("photos").is_faulty()


def test_reevaluate_clears_resolved_fault(tmp_path):
    unit, repo_root = make_unit(tmp_path, content={"a.txt": "alpha"})

    async def scenario():
        await unit.integrity_check()
        tamper_file(repo_root / "a.txt", "tampered")
        await unit.integrity_check()
        (repo_root / "a.txt").write_text("accepted", encoding="utf-8")
        return await unit.reevaluate_repository("photos")

    resolved = asyncio.run(scenario())
    manifest = unit.manifest("photos")
    assert resolved == 1
    assert not manifest.is_faulty()
    assert current_hash(manifest.files["a.txt"]) == hash_file(
        repo_root / "a.txt"
    )
    assert len(manifest.files["a.txt"]["history"]) == 2


def test_reevaluate_unknown_repository(tmp_path):
    unit, _ = make_unit(tmp_path)

    async def scenario():
        with pytest.raises(KeyError):
            await unit.reevaluate_repository("missing")

    asyncio.run(scenario())


# ------------------------------------------------------------------- HTTP


def test_status_endpoint(tmp_path):
    unit, _ = make_unit(tmp_path, content={"a.txt": "alpha"})

    async def scenario(base):
        await unit.integrity_check()
        return await _http(f"{base}/status")

    status, headers, body = with_server(unit, scenario)
    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    short_status, manifests = parse_status(body)
    assert short_status == STATUS_OK
    assert [manifest.name for manifest in manifests] == ["photos"]
    assert "a.txt" in manifests[0].files
    assert manifests[0].unit_id == unit.identifier


def test_status_endpoint_reports_faulty(tmp_path):
    unit, repo_root = make_unit(tmp_path, content={"a.txt": "alpha"})

    async def scenario(base):
        await unit.integrity_check()
        tamper_file(repo_root / "a.txt", "tampered")
        await unit.integrity_check()
        return await _http(f"{base}/status")

    status, _headers, body = with_server(unit, scenario)
    assert status == 200
    short_status, _manifests = parse_status(body)
    assert short_status == STATUS_FAULTY


def test_network_endpoint(tmp_path):
    unit, _ = make_unit(tmp_path)

    async def scenario(base):
        return await _http(f"{base}/network")

    status, headers, body = with_server(unit, scenario)
    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    units = parse_network_status(body)
    assert len(units) == 1  # single unit: only itself is known
    name, short_status, last_sync = units[0]
    assert name == unit.identifier
    assert short_status == STATUS_OK
    assert last_sync is not None
    # the unit reports its own data as up to date
    assert (datetime.now() - last_sync).total_seconds() < 5


def test_network_endpoint_reports_local_faulty_status(tmp_path):
    unit, repo_root = make_unit(tmp_path, content={"a.txt": "alpha"})

    async def scenario(base):
        await unit.integrity_check()
        tamper_file(repo_root / "a.txt", "tampered")
        await unit.integrity_check()
        return await _http(f"{base}/network")

    status, _headers, body = with_server(unit, scenario)
    assert status == 200
    name, short_status, _last_sync = parse_network_status(body)[0]
    assert name == unit.identifier
    assert short_status == STATUS_FAULTY


def test_summary_endpoint(tmp_path):
    unit, _ = make_unit(tmp_path, content={"a.txt": "alpha"})

    async def scenario(base):
        return await _http(f"{base}/summary")

    status, headers, body = with_server(unit, scenario)
    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert unit.identifier in body
    assert "photos" in body
    assert "Reevaluate integrity" not in body  # not faulty



def test_summary_endpoint_shows_reevaluate_button(tmp_path):
    unit, repo_root = make_unit(tmp_path, content={"a.txt": "alpha"})

    async def scenario(base):
        await unit.integrity_check()
        tamper_file(repo_root / "a.txt", "tampered")
        await unit.integrity_check()
        return await _http(f"{base}/summary")

    status, _headers, body = with_server(unit, scenario)
    assert status == 200
    assert "Reevaluate integrity" in body
    assert 'name="repository" value="photos"' in body


def test_root_redirects_to_summary(tmp_path):
    unit, _ = make_unit(tmp_path)

    async def scenario(base):
        return await _http(base + "/")

    status, _headers, body = with_server(unit, scenario)
    assert status == 200
    assert unit.identifier in body


def test_reevaluate_endpoint_query_param(tmp_path):
    unit, repo_root = make_unit(tmp_path, content={"a.txt": "alpha"})

    async def scenario(base):
        await unit.integrity_check()
        tamper_file(repo_root / "a.txt", "tampered")
        await unit.integrity_check()
        return await _http(f"{base}/reevaluate?repository=photos", data=b"")

    status, _headers, body = with_server(unit, scenario)
    assert status == 200
    assert "re-evaluated" in body
    # mtime still unchanged -> the fault is kept
    assert unit.manifest("photos").is_faulty()


def test_reevaluate_endpoint_form_body(tmp_path):
    unit, repo_root = make_unit(tmp_path, content={"a.txt": "alpha"})

    async def scenario(base):
        await unit.integrity_check()
        tamper_file(repo_root / "a.txt", "tampered")
        await unit.integrity_check()
        (repo_root / "a.txt").write_text("accepted", encoding="utf-8")
        return await _http(
            f"{base}/reevaluate",
            data=urllib.parse.urlencode({"repository": "photos"}).encode(),
        )

    status, _headers, body = with_server(unit, scenario)
    assert status == 200
    assert "1 fault issue(s) resolved" in body
    assert not unit.manifest("photos").is_faulty()


def test_reevaluate_endpoint_errors(tmp_path):
    unit, _ = make_unit(tmp_path)

    async def scenario(base):
        missing = await _http(f"{base}/reevaluate?repository=missing", data=b"")
        absent = await _http(f"{base}/reevaluate", data=b"")
        wrong_method = await _http(f"{base}/reevaluate")
        return missing, absent, wrong_method

    missing, absent, wrong_method = with_server(unit, scenario)
    assert missing[0] == 404
    assert "unknown repository" in missing[2]
    assert absent[0] == 400
    assert wrong_method[0] == 405


def test_unknown_path_and_method(tmp_path):
    unit, _ = make_unit(tmp_path)

    async def scenario(base):
        not_found = await _http(f"{base}/nope")
        bad_method = await _http(f"{base}/status", data=b"")
        return not_found, bad_method

    not_found, bad_method = with_server(unit, scenario)
    assert not_found[0] == 404
    assert bad_method[0] == 405


# --------------------------------------------------------------------- CLI


def test_main_rejects_invalid_config(tmp_path, capsys):
    path = tmp_path / "bad.yaml"
    path.write_text("port: not-a-number\n", encoding="utf-8")
    assert main(["--config", str(path)]) == 1
    assert "error:" in capsys.readouterr().err


def test_main_rejects_missing_config(tmp_path, capsys):
    assert main(["--config", str(tmp_path / "absent.yaml")]) == 1
    assert "error:" in capsys.readouterr().err


# ---------------------------------------------------------------- log verbosity


@pytest.fixture
def restore_log_levels():
    """Undo the root/``beacon`` levels that ``main`` configures."""
    saved = {name: logging.getLogger(name).level for name in ("", "beacon")}
    yield
    for name, level in saved.items():
        logging.getLogger(name).setLevel(level)


def write_valid_config(tmp_path):
    """Write a valid config file (the readme example) and return its path."""
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(valid_fields()), encoding="utf-8")
    return path


@pytest.fixture
def no_run(monkeypatch):
    """Stub out ``run``: ``main`` returns after configuring the logging."""

    async def stop_right_away(config):
        raise KeyboardInterrupt

    monkeypatch.setattr("unit.run", stop_right_away)


def test_main_defaults_to_info_logging(tmp_path, no_run, restore_log_levels):
    path = write_valid_config(tmp_path)
    assert main(["--config", str(path)]) == 0
    assert logging.getLogger("beacon").level == logging.INFO
    assert logging.getLogger("beacon.unit").getEffectiveLevel() == logging.INFO


def test_main_log_level_debug(tmp_path, no_run, restore_log_levels):
    path = write_valid_config(tmp_path)
    assert main(["--config", str(path), "--log-level", "DEBUG"]) == 0
    assert logging.getLogger("beacon.unit").getEffectiveLevel() == logging.DEBUG


def test_main_verbose_flag_enables_debug(tmp_path, no_run, restore_log_levels):
    path = write_valid_config(tmp_path)
    assert main(["--config", str(path), "--verbose"]) == 0
    assert logging.getLogger("beacon.unit").getEffectiveLevel() == logging.DEBUG
    assert main(["--config", str(path), "-v"]) == 0
    assert logging.getLogger("beacon.unit").getEffectiveLevel() == logging.DEBUG


def test_main_log_level_warning(tmp_path, no_run, restore_log_levels):
    path = write_valid_config(tmp_path)
    assert main(["--config", str(path), "--log-level", "warning"]) == 0
    assert (
        logging.getLogger("beacon.unit").getEffectiveLevel()
        == logging.WARNING
    )


def test_main_rejects_unknown_log_level(tmp_path, capsys):
    path = write_valid_config(tmp_path)
    with pytest.raises(SystemExit) as exit_info:
        main(["--config", str(path), "--log-level", "chatty"])
    assert exit_info.value.code == 2
    assert "--log-level" in capsys.readouterr().err

