"""Tests for the network message module (``scripts/network_messages.py``).

Covers the "network_messages.py interfaces" section of
``docs/software_architecture.md``:

- short statuses (``manifest_status``, ``compute_status``)
- uptime formatting (``format_uptime``)
- the base64/JSON manifest codec (``encode_manifest`` / ``decode_manifest``)
- the ``/status`` message (``generate_status`` / ``parse_status``)
- the ``/network`` message (``generate_network_status`` /
  ``parse_network_status``)
- the UDP ``discover`` message (``generate_discovery`` /
  ``parse_discovery``)
- the ``/summary`` HTML page (``generate_summary``)
"""

import base64
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from network_messages import (
    STATUS_CONFLICTS,
    STATUS_FAULTY,
    STATUS_OK,
    compute_status,
    decode_manifest,
    encode_manifest,
    format_uptime,
    generate_discovery,
    generate_network_status,
    generate_status,
    generate_summary,
    manifest_status,
    parse_discovery,
    parse_network_status,
    parse_status,
)
from conftest import make_manifest, make_metadata


def make_issue(issue_type, path, remote=None, description=""):
    return {
        "type": issue_type,
        "path": Path(path),
        "remote": remote,
        "description": description,
    }


def assert_manifests_equal(expected, actual):
    """Field-by-field manifest comparison (paths compared as PosixPath)."""
    assert actual.name == expected.name
    assert actual.path == Path(expected.path)
    assert actual.version == expected.version
    assert actual.unit_id == expected.unit_id
    assert actual.files == expected.files
    normalize = lambda issues: [
        {
            "type": issue["type"],
            "path": Path(issue["path"]),
            "remote": issue["remote"],
            "description": issue["description"],
        }
        for issue in issues
    ]
    assert normalize(actual.issues) == normalize(expected.issues)


def rich_manifest():
    """A manifest with files, both issue types and a unit id."""
    manifest = make_manifest(
        "documents",
        {
            "a.txt": make_metadata("h1"),
            "sub/b.bin": {
                "history": ["hash:h2", "hash:h3"],
                "timestamp": datetime(2026, 11, 5, 12, 34, 56),
            },
        },
        issues=[
            make_issue(
                "conflict",
                "a.txt",
                remote="laptop",
                description="diverged histories: local=h1, remote=h4",
            ),
            make_issue("fault", "sub/b.bin", description="cannot read file"),
        ],
        path="/data/documents",
    )
    manifest.unit_id = "pc"
    return manifest


# ------------------------------------------------------------------- statuses


def test_manifest_status_ok():
    assert manifest_status(make_manifest("repo")) == STATUS_OK


def test_manifest_status_faulty():
    manifest = make_manifest("repo", issues=[make_issue("fault", "a.txt")])
    assert manifest_status(manifest) == STATUS_FAULTY


def test_manifest_status_conflicts():
    manifest = make_manifest(
        "repo", issues=[make_issue("conflict", "a.txt", remote="peer")]
    )
    assert manifest_status(manifest) == STATUS_CONFLICTS


def test_manifest_status_faulty_beats_conflicts():
    manifest = make_manifest(
        "repo",
        issues=[
            make_issue("conflict", "a.txt", remote="peer"),
            make_issue("fault", "b.txt"),
        ],
    )
    assert manifest_status(manifest) == STATUS_FAULTY


def test_compute_status_empty():
    assert compute_status([]) == STATUS_OK


def test_compute_status_ok():
    manifests = [make_manifest("a"), make_manifest("b")]
    assert compute_status(manifests) == STATUS_OK


def test_compute_status_conflicts():
    manifests = [
        make_manifest("a"),
        make_manifest(
            "b", issues=[make_issue("conflict", "x", remote="peer")]
        ),
    ]
    assert compute_status(manifests) == STATUS_CONFLICTS


def test_compute_status_faulty_wins():
    manifests = [
        make_manifest(
            "a", issues=[make_issue("conflict", "x", remote="peer")]
        ),
        make_manifest("b", issues=[make_issue("fault", "y")]),
    ]
    assert compute_status(manifests) == STATUS_FAULTY


# -------------------------------------------------------------------- uptime


def test_format_uptime_zero():
    assert format_uptime(0) == "0 seconds"


def test_format_uptime_seconds_only():
    assert format_uptime(59) == "59 seconds"


def test_format_uptime_hours_minutes_seconds():
    assert format_uptime(3661) == "1 hour, 1 minute, 1 second"


def test_format_uptime_all_units():
    assert format_uptime(93784) == "1 day, 2 hours, 3 minutes, 4 seconds"


def test_format_uptime_pluralization():
    assert format_uptime(7200) == "2 hours"
    assert format_uptime(172800) == "2 days"


def test_format_uptime_singular():
    assert format_uptime(60) == "1 minute"


@pytest.mark.parametrize("value", [-1, 1.5, "90", None, True])
def test_format_uptime_rejects_invalid(value):
    with pytest.raises(ValueError):
        format_uptime(value)


# ------------------------------------------------------------- manifest codec


def test_encode_decode_round_trip():
    manifest = rich_manifest()
    decoded = decode_manifest(encode_manifest(manifest))
    assert_manifests_equal(manifest, decoded)


def test_encode_manifest_deterministic():
    manifest = rich_manifest()
    assert encode_manifest(manifest) == encode_manifest(manifest)


def test_encode_manifest_payload_layout():
    metadata = make_metadata("h1")
    manifest = make_manifest("repo", {"a.txt": metadata})
    payload = json.loads(base64.b64decode(encode_manifest(manifest)))
    assert payload["version"] == 1
    assert payload["name"] == "repo"
    assert payload["path"] == "/data/repo"
    assert payload["unit_id"] is None
    assert payload["files"]["a.txt"]["history"] == ["hash:h1"]
    assert datetime.fromisoformat(
        payload["files"]["a.txt"]["timestamp"]
    ) == metadata["timestamp"]
    assert payload["issues"] == []


def test_decode_manifest_rejects_non_string():
    with pytest.raises(ValueError, match="must be a string"):
        decode_manifest(b"abc")


def test_decode_manifest_rejects_bad_base64():
    with pytest.raises(ValueError, match="base64"):
        decode_manifest("!!!not base64!!!")


def test_decode_manifest_rejects_non_json():
    encoded = base64.b64encode(b"this is not json").decode("ascii")
    with pytest.raises(ValueError, match="JSON"):
        decode_manifest(encoded)


def encode_payload(payload):
    return base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")


def payload_of(manifest):
    return json.loads(base64.b64decode(encode_manifest(manifest)))


def test_decode_manifest_rejects_wrong_version():
    payload = payload_of(make_manifest("repo"))
    payload["version"] = 99
    with pytest.raises(ValueError, match="version"):
        decode_manifest(encode_payload(payload))


@pytest.mark.parametrize("field", ["version", "name", "path", "files", "issues"])
def test_decode_manifest_rejects_missing_field(field):
    payload = payload_of(make_manifest("repo"))
    del payload[field]
    with pytest.raises(ValueError):
        decode_manifest(encode_payload(payload))


def test_decode_manifest_rejects_bad_history():
    payload = {
        "version": 1,
        "name": "repo",
        "path": "/data/repo",
        "unit_id": None,
        "files": {
            "a.txt": {
                "history": "not-a-list",
                "timestamp": "2026-01-01T00:00:00",
            }
        },
        "issues": [],
    }
    with pytest.raises(ValueError, match="history"):
        decode_manifest(encode_payload(payload))


def test_decode_manifest_rejects_bad_timestamp():
    payload = {
        "version": 1,
        "name": "repo",
        "path": "/data/repo",
        "unit_id": None,
        "files": {
            "a.txt": {"history": ["hash:h1"], "timestamp": "yesterday"}
        },
        "issues": [],
    }
    with pytest.raises(ValueError, match="timestamp"):
        decode_manifest(encode_payload(payload))


def test_decode_manifest_rejects_bad_issue_type():
    payload = {
        "version": 1,
        "name": "repo",
        "path": "/data/repo",
        "unit_id": None,
        "files": {},
        "issues": [
            {
                "type": "mystery",
                "path": "a.txt",
                "remote": None,
                "description": "",
            }
        ],
    }
    with pytest.raises(ValueError, match="issue type"):
        decode_manifest(encode_payload(payload))


def test_decode_manifest_rejects_non_string_issue_path():
    payload = {
        "version": 1,
        "name": "repo",
        "path": "/data/repo",
        "unit_id": None,
        "files": {},
        "issues": [
            {
                "type": "fault",
                "path": 42,
                "remote": None,
                "description": "",
            }
        ],
    }
    with pytest.raises(ValueError, match="issue path"):
        decode_manifest(encode_payload(payload))


# ------------------------------------------------------------ status message


def test_generate_status_empty():
    message = generate_status([])
    assert json.loads(message) == {"status": STATUS_OK, "manifests": []}
    assert parse_status(message) == (STATUS_OK, [])


def test_generate_status_layout():
    message = generate_status([make_manifest("a"), make_manifest("b")])
    data = json.loads(message)
    assert set(data) == {"status", "manifests"}
    assert data["status"] == STATUS_OK
    assert len(data["manifests"]) == 2
    for encoded in data["manifests"]:
        assert isinstance(encoded, str)
        base64.b64decode(encoded, validate=True)


def test_generate_status_reports_faulty():
    manifests = [
        make_manifest("a"),
        make_manifest("b", issues=[make_issue("fault", "f")]),
    ]
    assert json.loads(generate_status(manifests))["status"] == STATUS_FAULTY


def test_generate_status_reports_conflicts():
    manifests = [
        make_manifest("a", issues=[make_issue("conflict", "f", remote="u")])
    ]
    assert json.loads(generate_status(manifests))["status"] == STATUS_CONFLICTS


def test_generate_status_rejects_non_manifest():
    with pytest.raises(ValueError, match="Manifest"):
        generate_status(["not a manifest"])


def test_parse_status_round_trip():
    manifests = [rich_manifest(), make_manifest("photos")]
    status, decoded = parse_status(generate_status(manifests))
    assert status == STATUS_FAULTY
    assert len(decoded) == 2
    assert_manifests_equal(manifests[0], decoded[0])
    assert_manifests_equal(manifests[1], decoded[1])


def test_parse_status_round_trip_ok():
    manifests = [make_manifest("a"), make_manifest("b")]
    status, decoded = parse_status(generate_status(manifests))
    assert status == STATUS_OK
    assert [m.name for m in decoded] == ["a", "b"]


def test_parse_status_rejects_non_string():
    with pytest.raises(ValueError, match="must be a string"):
        parse_status({"status": "OK"})


def test_parse_status_rejects_invalid_json():
    with pytest.raises(ValueError, match="JSON"):
        parse_status("{not json")


def test_parse_status_rejects_non_object():
    with pytest.raises(ValueError, match="JSON object"):
        parse_status("[1, 2, 3]")


def test_parse_status_rejects_unknown_status():
    with pytest.raises(ValueError, match="unknown status"):
        parse_status('{"status": "broken", "manifests": []}')


def test_parse_status_rejects_missing_manifests():
    with pytest.raises(ValueError, match="'manifests'"):
        parse_status('{"status": "OK"}')


def test_parse_status_rejects_bad_manifest_entry():
    with pytest.raises(ValueError, match="base64"):
        parse_status('{"status": "OK", "manifests": ["!!!"]}')


# ---------------------------------------------------------- network message


def sample_remotes():
    return [
        (
            "laptop",
            STATUS_OK,
            datetime(2026, 11, 5, 2, 0, 0, tzinfo=timezone.utc),
        ),
        ("server", STATUS_FAULTY, None),
        ("desktop", STATUS_CONFLICTS, datetime(2026, 11, 4, 23, 45)),
    ]


def test_network_status_round_trip():
    remotes = sample_remotes()
    decoded = parse_network_status(generate_network_status(remotes))
    assert decoded == remotes


def test_network_status_layout():
    message = generate_network_status(
        [
            (
                "laptop",
                STATUS_OK,
                datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
        ]
    )
    assert json.loads(message) == [
        {
            "name": "laptop",
            "status": "OK",
            "last_sync": "2026-01-01T00:00:00+00:00",
        }
    ]


def test_network_status_empty():
    assert generate_network_status([]) == "[]"
    assert parse_network_status("[]") == []


def test_parse_network_status_rejects_non_string():
    with pytest.raises(ValueError, match="must be a string"):
        parse_network_status([{"name": "a"}])


def test_parse_network_status_rejects_invalid_json():
    with pytest.raises(ValueError, match="JSON"):
        parse_network_status("not json")


def test_parse_network_status_rejects_non_list():
    with pytest.raises(ValueError, match="JSON list"):
        parse_network_status('{"name": "a"}')


def test_parse_network_status_rejects_non_object_entry():
    with pytest.raises(ValueError, match="JSON objects"):
        parse_network_status('["a"]')


def test_parse_network_status_rejects_missing_name():
    with pytest.raises(ValueError, match="name"):
        parse_network_status('[{"status": "OK"}]')


def test_parse_network_status_rejects_unknown_status():
    with pytest.raises(ValueError, match="unknown status"):
        parse_network_status('[{"name": "a", "status": "broken"}]')


def test_parse_network_status_rejects_bad_last_sync():
    with pytest.raises(ValueError, match="ISO 8601"):
        parse_network_status(
            '[{"name": "a", "status": "OK", "last_sync": "yesterday"}]'
        )


def test_generate_network_status_rejects_short_tuple():
    with pytest.raises(ValueError, match="tuples"):
        generate_network_status([("a", STATUS_OK)])


def test_generate_network_status_rejects_empty_name():
    with pytest.raises(ValueError, match="name"):
        generate_network_status([("  ", STATUS_OK, None)])


def test_generate_network_status_rejects_unknown_status():
    with pytest.raises(ValueError, match="unknown status"):
        generate_network_status([("a", "broken", None)])


def test_generate_network_status_rejects_bad_sync_time():
    with pytest.raises(ValueError, match="datetime"):
        generate_network_status([("a", STATUS_OK, "2026-01-01T00:00:00")])


# ------------------------------------------------------------- summary page


def summary_fixtures():
    """One OK, one faulty and one conflicting manifest plus remotes."""
    manifests = [
        make_manifest("photos", {"a.txt": make_metadata("h1")}),
        make_manifest(
            "documents",
            {},
            issues=[
                make_issue("fault", "missing.txt", description="cannot read")
            ],
        ),
        make_manifest(
            "notes",
            {"n.txt": make_metadata("h9")},
            issues=[
                make_issue(
                    "conflict",
                    "n.txt",
                    remote="laptop",
                    description="diverged histories",
                )
            ],
        ),
    ]
    remotes = [
        ("laptop", STATUS_OK, datetime(2026, 11, 5, 2, 0, 0)),
        ("server", STATUS_CONFLICTS, None),
    ]
    return manifests, remotes


def test_summary_contains_unit_and_uptime():
    page = generate_summary("pc", 93784, [], [])
    assert "<title>pc</title>" in page
    assert "Backup unit: pc" in page
    assert "Uptime: 1 day, 2 hours, 3 minutes, 4 seconds" in page


def test_summary_lists_repositories_with_status():
    manifests, remotes = summary_fixtures()
    page = generate_summary("pc", 60, manifests, remotes)
    for name, status in (
        ("photos", "OK"),
        ("documents", "faulty"),
        ("notes", "conflicts"),
    ):
        assert f"<tr><td>{name}</td><td>{status}</td>" in page


def test_summary_conflicts_show_file_and_cause():
    manifests, _ = summary_fixtures()
    page = generate_summary("pc", 60, manifests, [])
    assert "<h2>Conflicts</h2>" in page
    assert "n.txt" in page
    assert "laptop" in page
    assert "diverged histories" in page


def test_summary_has_no_conflicts_section_when_clean():
    page = generate_summary("pc", 60, [make_manifest("photos")], [])
    assert "<h2>Conflicts</h2>" not in page


def test_summary_faulty_repo_has_reevaluate_button():
    manifests, _ = summary_fixtures()
    page = generate_summary("pc", 60, manifests, [])
    assert '<form method="post" action="/reevaluate">' in page
    assert 'name="repository" value="documents"' in page
    assert "Reevaluate integrity" in page
    # only the faulty repository gets a button
    assert page.count("<form") == 1


def test_summary_remotes_table():
    _, remotes = summary_fixtures()
    page = generate_summary("pc", 60, [], remotes)
    assert (
        "<tr><td>laptop</td><td>OK</td><td>2026-11-05T02:00:00</td></tr>"
        in page
    )
    assert "<tr><td>server</td><td>conflicts</td><td>never</td></tr>" in page


def test_summary_empty_state_messages():
    page = generate_summary("pc", 0, [], [])
    assert "Uptime: 0 seconds" in page
    assert "This unit does not manage any repositories." in page
    assert "No other units are known." in page


def test_summary_escapes_html():
    evil = make_manifest("<script>alert(1)</script>")
    page = generate_summary("pc", 0, [evil], [])
    assert "<script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page


def test_summary_escapes_repository_name_in_form():
    manifest = make_manifest('x"y', issues=[make_issue("fault", "a.txt")])
    page = generate_summary("pc", 0, [manifest], [])
    assert 'value="x&quot;y"' in page


def test_summary_rejects_invalid_arguments():
    with pytest.raises(ValueError):
        generate_summary("", 60, [], [])
    with pytest.raises(ValueError):
        generate_summary("pc", -1, [], [])
    with pytest.raises(ValueError):
        generate_summary("pc", 60, ["not a manifest"], [])
    with pytest.raises(ValueError):
        generate_summary("pc", 60, [], [("a", "broken", None)])


# ------------------------------------------------------------ discovery message


def test_generate_discovery_format():
    message = generate_discovery(
        "laptop", 8091, 8090, "Hello my backup network"
    )
    assert json.loads(message) == {
        "unit_id": "laptop",
        "unit_port": 8091,
        "tcp_port": 8090,
        "trusted_token": "Hello my backup network",
    }


def test_discovery_round_trip():
    message = generate_discovery("laptop", 8091, 8090, "token")
    assert parse_discovery(message) == ("laptop", 8091, 8090, "token")


@pytest.mark.parametrize("identifier", ("", "   ", None, 42))
def test_generate_discovery_rejects_identifier(identifier):
    with pytest.raises(
        ValueError, match="unit identifier must be a non-empty"
    ):
        generate_discovery(identifier, 8091, 8090, "token")


@pytest.mark.parametrize("port", ("8091", 8091.5, True, 0, -1, 65536, [8091]))
def test_generate_discovery_rejects_ports(port):
    with pytest.raises(ValueError, match="unit_port must be"):
        generate_discovery("laptop", port, 8090, "token")
    with pytest.raises(ValueError, match="tcp_port must be"):
        generate_discovery("laptop", 8091, port, "token")


def test_generate_discovery_rejects_non_string_token():
    with pytest.raises(ValueError, match="trusted token must be a string"):
        generate_discovery("laptop", 8091, 8090, 42)


def test_parse_discovery_rejects_non_string():
    with pytest.raises(ValueError, match="discovery message must be a string"):
        parse_discovery(b'{"unit_id": "laptop"}')


def test_parse_discovery_rejects_invalid_json():
    with pytest.raises(ValueError, match="discovery message is not valid JSON"):
        parse_discovery("nonsense")


def test_parse_discovery_rejects_non_object():
    with pytest.raises(
        ValueError, match="discovery message must be a JSON object"
    ):
        parse_discovery("[1, 2]")


@pytest.mark.parametrize(
    "missing", ("unit_id", "unit_port", "tcp_port", "trusted_token")
)
def test_parse_discovery_rejects_missing_field(missing):
    message = json.loads(generate_discovery("laptop", 8091, 8090, "token"))
    del message[missing]
    if missing == "unit_id":
        pattern = "missing a valid unit id"
    else:
        pattern = rf"missing the '{missing}' field"
    with pytest.raises(ValueError, match=pattern):
        parse_discovery(json.dumps(message))


@pytest.mark.parametrize("unit_id", ("", 42, None))
def test_parse_discovery_rejects_bad_unit_id(unit_id):
    with pytest.raises(ValueError, match="missing a valid unit id"):
        parse_discovery(
            json.dumps(
                {
                    "unit_id": unit_id,
                    "unit_port": 8091,
                    "tcp_port": 8090,
                    "trusted_token": "token",
                }
            )
        )


@pytest.mark.parametrize("port", ("8091", 0, 65536, True, None))
def test_parse_discovery_rejects_bad_ports(port):
    for field in ("unit_port", "tcp_port"):
        message = json.loads(generate_discovery("laptop", 8091, 8090, "token"))
        message[field] = port
        with pytest.raises(ValueError, match="must be an integer|in range"):
            parse_discovery(json.dumps(message))


def test_parse_discovery_rejects_non_string_token():
    message = json.loads(generate_discovery("laptop", 8091, 8090, "token"))
    message["trusted_token"] = 42
    with pytest.raises(ValueError, match="trusted token must be a string"):
        parse_discovery(json.dumps(message))
