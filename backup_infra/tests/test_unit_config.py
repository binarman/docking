"""Tests for the Beacon unit config module (``scripts/unit_config.py``).

Covers the "Config file" section of the readme:

- loading a YAML config file (``UnitConfig.load``)
- constructing from arguments (``UnitConfig(...)``) with the same checks
- required fields, types and value ranges
- mutual exclusion of ``*_delay`` / ``*_schedule`` pairs
- 5-field cron schedule format rules
- ``repository`` list rules (non-empty, ``name``/``path``, unique names)
- rejection of unknown fields
"""

import pytest
import yaml

from unit_config import Repository, UnitConfig, validate_schedule

#: Sentinel meaning "drop this field" in ``load_config`` overrides.
OMIT = object()


def valid_fields():
    """A minimal fully-valid config as a plain dict (fresh on each call)."""
    return {
        "port": 8090,
        "address": "0.0.0.0",
        "identifier": "laptop",
        "trusted_token": "Hello my backup network",
        "sync_delay": 86400,
        "integrity_schedule": "45 23 * * 6",
        "repository": [
            {"name": "photos", "path": "/data/photos"},
            {"name": "documents", "path": "/data/documents"},
        ],
        "manifest_storage": "/data/manifests",
    }


def load_config(tmp_path, **overrides):
    """Write ``valid_fields()`` plus ``overrides`` to YAML and load it.

    Pass ``OMIT`` as an override value to drop the field entirely.
    """
    fields = valid_fields()
    for key, value in overrides.items():
        if value is OMIT:
            fields.pop(key, None)
        else:
            fields[key] = value
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(fields), encoding="utf-8")
    return UnitConfig.load(path)


def minimal_kwargs(**overrides):
    """A fully-valid keyword dict for ``UnitConfig`` (sync by schedule,
    integrity by delay), overridable per test."""
    kwargs = {
        "port": 8090,
        "identifier": "laptop",
        "trusted_token": "token",
        "sync_schedule": "0 0 * * 0",
        "integrity_delay": 604800,
        "repository": [{"name": "photos", "path": "/data/photos"}],
        "manifest_storage": "/data/manifests",
    }
    kwargs.update(overrides)
    return kwargs


# ------------------------------------------------------------------ loading


def test_load_valid_yaml(tmp_path):
    config = load_config(tmp_path)
    assert config.port == 8090
    assert config.address == "0.0.0.0"
    assert config.identifier == "laptop"
    assert config.trusted_token == "Hello my backup network"
    assert config.sync_delay == 86400
    assert config.sync_schedule is None
    assert config.integrity_delay is None
    assert config.integrity_schedule == "45 23 * * 6"
    assert config.repository == [
        Repository("photos", "/data/photos"),
        Repository("documents", "/data/documents"),
    ]
    assert config.manifest_storage == "/data/manifests"


def test_load_readme_example(tmp_path):
    # The readme example plus the required ``manifest_storage`` field.
    path = tmp_path / "config.yaml"
    path.write_text(
        """\
port: 8090
address: 0.0.0.0
identifier: laptop
trusted_token: "Hello my backup network"
sync_delay: 86400
integrity_schedule: "45 23 * * 6"
repository:
  - name: photos
    path: /data/photos
  - name: documents
    path: /data/documents
manifest_storage: /data/manifests
""",
        encoding="utf-8",
    )
    config = UnitConfig.load(path)
    assert config.identifier == "laptop"
    assert [repo.name for repo in config.repository] == ["photos", "documents"]


def test_load_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        UnitConfig.load(tmp_path / "absent.yaml")


def test_load_empty_file(tmp_path):
    (tmp_path / "config.yaml").write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="empty config file"):
        UnitConfig.load(tmp_path / "config.yaml")


def test_load_non_mapping_root(tmp_path):
    (tmp_path / "config.yaml").write_text("- port: 8090\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a mapping"):
        UnitConfig.load(tmp_path / "config.yaml")


def test_load_malformed_yaml(tmp_path):
    (tmp_path / "config.yaml").write_text("port: [8090\n", encoding="utf-8")
    with pytest.raises(yaml.YAMLError):
        UnitConfig.load(tmp_path / "config.yaml")


def test_load_rejects_unknown_field(tmp_path):
    with pytest.raises(ValueError, match=r"unknown config field\(s\): 'bogus'"):
        load_config(tmp_path, bogus=1)


def test_load_rejects_multiple_unknown_fields(tmp_path):
    # yaml.safe_dump sorts keys, so the reported order is alphabetical.
    with pytest.raises(
        ValueError,
        match=r"unknown config field\(s\): 'another', 'bogus'",
    ):
        load_config(tmp_path, bogus=1, another=2)


def test_load_rejects_both_sync_members(tmp_path):
    with pytest.raises(ValueError, match="mutually exclusive"):
        load_config(tmp_path, sync_schedule="45 23 * * 6")


def test_load_requires_sync_member(tmp_path):
    with pytest.raises(
        ValueError, match="at least one of sync_delay and sync_schedule"
    ):
        load_config(tmp_path, sync_delay=OMIT)


def test_load_requires_integrity_member(tmp_path):
    with pytest.raises(
        ValueError,
        match="at least one of integrity_delay and integrity_schedule",
    ):
        load_config(tmp_path, integrity_schedule=OMIT)



# ------------------------------------------------------------ constructor


def test_constructor_from_arguments():
    config = UnitConfig(
        port=8090,
        address="192.168.1.10",
        identifier="pc",
        trusted_token="token",
        sync_schedule="0 0 * * 0",
        integrity_delay=604800,
        repository=[{"name": "photos", "path": "/data/photos"}],
        manifest_storage="/data/manifests",
    )
    assert config.port == 8090
    assert config.address == "192.168.1.10"
    assert config.identifier == "pc"
    assert config.trusted_token == "token"
    assert config.sync_delay is None
    assert config.sync_schedule == "0 0 * * 0"
    assert config.integrity_delay == 604800
    assert config.integrity_schedule is None
    assert config.repository == [Repository("photos", "/data/photos")]
    assert config.manifest_storage == "/data/manifests"


def test_optional_fields_default_to_none():
    config = UnitConfig(**minimal_kwargs())
    assert config.address is None
    assert config.sync_delay is None
    assert config.integrity_schedule is None


@pytest.mark.parametrize(
    "field", ("port", "identifier", "trusted_token", "repository", "manifest_storage")
)
def test_missing_required_field(field):
    kwargs = minimal_kwargs()
    del kwargs[field]
    with pytest.raises(ValueError, match=f"missing required field: {field}"):
        UnitConfig(**kwargs)


@pytest.mark.parametrize("port", ("8090", 8090.5, True, [8090]))
def test_port_must_be_an_integer(port):
    with pytest.raises(ValueError, match="port must be an integer"):
        UnitConfig(**minimal_kwargs(port=port))


@pytest.mark.parametrize("port", (-1, 0, 65536, 1 << 32))
def test_port_out_of_range(port):
    with pytest.raises(ValueError, match="port must be in range 1-65535"):
        UnitConfig(**minimal_kwargs(port=port))


@pytest.mark.parametrize(
    "field", ("identifier", "trusted_token", "manifest_storage", "address")
)
def test_string_fields_must_be_strings(field):
    kwargs = minimal_kwargs(address="0.0.0.0")
    kwargs[field] = 123
    with pytest.raises(ValueError, match="must be a string"):
        UnitConfig(**kwargs)


@pytest.mark.parametrize(
    "field", ("identifier", "trusted_token", "manifest_storage")
)
def test_required_string_fields_must_not_be_empty(field):
    with pytest.raises(ValueError, match="must not be empty"):
        UnitConfig(**minimal_kwargs(**{field: "   "}))


def test_address_must_not_be_empty_when_given():
    with pytest.raises(ValueError, match="must not be empty"):
        UnitConfig(**minimal_kwargs(address="  "))


@pytest.mark.parametrize("value", (0, -100, "86400", 86400.5, True))
def test_sync_delay_must_be_positive_integer(value):
    with pytest.raises(ValueError, match=r"must be (a positive |an )?integer"):
        UnitConfig(**minimal_kwargs(sync_delay=value, sync_schedule=None))


@pytest.mark.parametrize("value", (0, -100, "604800", 604800.5, True))
def test_integrity_delay_must_be_positive_integer(value):
    with pytest.raises(ValueError, match=r"must be (a positive |an )?integer"):
        UnitConfig(**minimal_kwargs(integrity_delay=value))


def test_delay_values_are_accessible():
    config = UnitConfig(**minimal_kwargs(sync_delay=3600, sync_schedule=None))
    assert config.sync_delay == 3600
    assert config.integrity_delay == 604800


def test_delay_and_schedule_are_mutually_exclusive():
    with pytest.raises(ValueError, match="mutually exclusive"):
        UnitConfig(**minimal_kwargs(sync_delay=86400))
    with pytest.raises(ValueError, match="mutually exclusive"):
        UnitConfig(**minimal_kwargs(integrity_schedule="45 23 * * 6"))


def test_at_least_one_of_each_pair_is_required():
    with pytest.raises(
        ValueError, match="at least one of sync_delay and sync_schedule"
    ):
        UnitConfig(**minimal_kwargs(sync_schedule=None))
    with pytest.raises(
        ValueError, match="at least one of integrity_delay and integrity_schedule"
    ):
        UnitConfig(**minimal_kwargs(integrity_delay=None))


# ----------------------------------------------------------------- schedule


@pytest.mark.parametrize(
    "schedule",
    [
        "* * * * *",
        "45 23 * * 6",
        "0 0 1 1 0",
        "59 23 31 12 6",
        "05 03 01 01 00",
        "1 2 3 4 5",
        " 45  23 * * 6",
    ],
)
def test_valid_schedules(schedule):
    assert validate_schedule(schedule) == schedule


@pytest.mark.parametrize(
    "schedule",
    [
        "",               # no fields
        "* * * *",         # 4 fields
        "* * * * * *",     # 6 fields
        "a * * * *",       # non-numeric
        "60 * * * *",      # minute > 59
        "* 24 * * *",      # hour > 23
        "* * 0 * *",       # day < 1
        "* * 32 * *",      # day > 31
        "* * * 0 *",       # month < 1
        "* * * 13 *",      # month > 12
        "* * * * 7",       # weekday > 6
        "-1 * * * *",      # negative
        "1.5 * * * *",     # not an integer
        "*/5 * * * *",     # steps not supported
        "1-5 * * * *",     # ranges not supported
        "1,15 * * * *",    # lists not supported
        "sun * * * *",     # names not supported
    ],
)
def test_invalid_schedules(schedule):
    with pytest.raises(ValueError):
        validate_schedule(schedule)


def test_schedule_must_be_string():
    with pytest.raises(ValueError, match="must be a string"):
        validate_schedule(["45", "23", "*", "*", "6"])


def test_schedule_errors_name_the_offending_field():
    with pytest.raises(ValueError, match="minute out of range"):
        validate_schedule("60 * * * *")
    with pytest.raises(ValueError, match="day of the week out of range"):
        validate_schedule("* * * * 7")


def test_config_rejects_invalid_schedule():
    with pytest.raises(ValueError, match="hour out of range"):
        UnitConfig(**minimal_kwargs(sync_schedule="0 24 * * 0"))


def test_config_rejects_non_string_schedule():
    with pytest.raises(ValueError, match="sync_schedule must be a string"):
        UnitConfig(**minimal_kwargs(sync_schedule=["0", "0", "*", "*", "0"]))


# ------------------------------------------------------------- repository


def test_repository_entries_expose_name_and_path():
    config = UnitConfig(**minimal_kwargs())
    (photos,) = config.repository
    assert (photos.name, photos.path) == ("photos", "/data/photos")


def test_repository_accepts_repository_instances():
    config = UnitConfig(
        **minimal_kwargs(repository=[Repository("photos", "/data/photos")])
    )
    assert config.repository == [Repository("photos", "/data/photos")]


def test_repository_list_is_protected():
    config = UnitConfig(**minimal_kwargs())
    config.repository.append(Repository("extra", "/extra"))
    assert [repo.name for repo in config.repository] == ["photos"]


def test_repository_must_be_a_list():
    with pytest.raises(ValueError, match="repository must be a list"):
        UnitConfig(**minimal_kwargs(repository="photos"))
    with pytest.raises(ValueError, match="repository must be a list"):
        UnitConfig(
            **minimal_kwargs(repository={"name": "photos", "path": "/data/photos"})
        )


def test_repository_must_not_be_empty():
    with pytest.raises(ValueError, match="at least one entry"):
        UnitConfig(**minimal_kwargs(repository=[]))


def test_repository_entry_must_be_a_mapping():
    with pytest.raises(ValueError, match=r"repository\[0\] must be a mapping"):
        UnitConfig(**minimal_kwargs(repository=["photos"]))


@pytest.mark.parametrize("missing", ("name", "path"))
def test_repository_entry_requires_name_and_path(missing):
    entry = {"name": "photos", "path": "/data/photos"}
    del entry[missing]
    with pytest.raises(
        ValueError,
        match=rf"missing required field: repository\[0\]\.{missing}",
    ):
        UnitConfig(**minimal_kwargs(repository=[entry]))


def test_repository_entry_rejects_unknown_fields():
    with pytest.raises(ValueError, match=r"unknown field\(s\): 'storage'"):
        UnitConfig(
            **minimal_kwargs(
                repository=[{"name": "photos", "path": "/data/photos", "storage": "x"}]
            )
        )


def test_repository_names_must_be_unique():
    with pytest.raises(ValueError, match="duplicate repository name"):
        UnitConfig(
            **minimal_kwargs(
                repository=[
                    {"name": "photos", "path": "/data/a"},
                    {"name": "photos", "path": "/data/b"},
                ]
            )
        )


# ---------------------------------------------------------------- discovery

def test_discovery_fields_default_to_none():
    config = UnitConfig(**minimal_kwargs())
    assert config.discovery_port is None
    assert config.discovery_address is None


def test_discovery_fields_are_accessible():
    config = UnitConfig(
        **minimal_kwargs(discovery_port=8091, discovery_address="192.168.1.255")
    )
    assert config.discovery_port == 8091
    assert config.discovery_address == "192.168.1.255"


@pytest.mark.parametrize("value", ("8090", 8090.5, True, [8090]))
def test_discovery_port_must_be_an_integer(value):
    with pytest.raises(ValueError, match="discovery_port must be an integer"):
        UnitConfig(**minimal_kwargs(discovery_port=value))


@pytest.mark.parametrize("value", (-1, 0, 65536, 1 << 32))
def test_discovery_port_out_of_range(value):
    with pytest.raises(
        ValueError, match="discovery_port must be in range 1-65535"
    ):
        UnitConfig(**minimal_kwargs(discovery_port=value))


@pytest.mark.parametrize("value", (123, ["192.168.1.255"]))
def test_discovery_address_must_be_a_string(value):
    with pytest.raises(ValueError, match="discovery_address must be a string"):
        UnitConfig(**minimal_kwargs(discovery_address=value))


def test_discovery_address_must_not_be_empty():
    with pytest.raises(ValueError, match="discovery_address must not be empty"):
        UnitConfig(**minimal_kwargs(discovery_address="  "))


def test_discovery_fields_load_from_yaml(tmp_path):
    config = load_config(
        tmp_path, discovery_port=8091, discovery_address="192.168.1.255"
    )
    assert config.discovery_port == 8091
    assert config.discovery_address == "192.168.1.255"

