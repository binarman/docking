"""Tests for the install wizard (``scripts/install.py``).

Covers the "Unit configuration" section of the readme:

- menu navigation and scalar field editing (port, strings, delay, schedule)
- the optional network discovery fields (``discovery_port``,
  ``discovery_address``): validation, prompts and save round-trip
- inline validation before a value is accepted
- mutual exclusion of ``*_delay`` / ``*_schedule`` pairs
- the repository submenu (add / edit / delete, unique names)
- save behaviour: full ``UnitConfig`` validation before writing,
  overwrite confirmation, YAML round-trip via ``UnitConfig.load``
- ``main``: loading an existing config as the starting point and
  graceful handling of EOF
"""

import sys

import yaml

import install
from install import InstallWizard
from unit_config import UnitConfig


def run_wizard(config_path, answers, values=None):
    """Run a wizard fed by the ``answers`` list; return (wizard, output)."""
    output = []
    iterator = iter(answers)
    wizard = InstallWizard(
        config_path=str(config_path),
        values=values,
        input_func=lambda _: next(iterator),
        output_func=output.append,
    )
    wizard.run()
    return wizard, output


def valid_yaml(tmp_path):
    """A fully-valid config file path (sync by delay, integrity by schedule)."""
    fields = {
        "port": 8090,
        "identifier": "laptop",
        "trusted_token": "token",
        "sync_delay": 86400,
        "integrity_schedule": "45 23 * * 6",
        "repository": [{"name": "photos", "path": "/data/photos"}],
        "manifest_storage": "/data/manifests",
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(fields), encoding="utf-8")
    return path


def field_index(key):
    """The 0-based position of a menu field (the menu mirrors the config)."""
    return [entry.key for entry in install.MENU_FIELDS].index(key)


def field_no(key):
    """The wizard menu number of a config field."""
    return str(field_index(key) + 1)


# ---------------------------------------------------------------- menu flow


def test_menu_lists_the_discovery_fields(tmp_path):
    path = tmp_path / "config.yaml"
    _, output = run_wizard(path, ["q"])
    joined = "\n".join(output)
    assert f"{field_no('discovery_port'):>2}. discovery_port" in joined
    assert f"{field_no('discovery_address'):>2}. discovery_address" in joined
    # discovery is optional: unset fields are offered as such
    assert "discovery_port     : (not set)" in joined
    assert "discovery_address  : (not set)" in joined


def test_full_save_round_trip(tmp_path):
    path = tmp_path / "out" / "config.yaml"
    answers = [
        field_no("port"), "8080",
        field_no("identifier"), "id-1",
        field_no("trusted_token"), "tok-abc",
        field_no("sync_schedule"), "5 * * * *",
        field_no("integrity_delay"), "30",
        field_no("repository"), "a", "repo-a", "/repos/a", "q",
        field_no("manifest_storage"), "/units/unit1/manifests",
        "s", "q",  # save, quit
    ]
    wizard, output = run_wizard(path, answers)

    assert wizard.saved
    assert "Unit config saved to" in "\n".join(output)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data == {
        "port": 8080,
        "identifier": "id-1",
        "trusted_token": "tok-abc",
        "sync_schedule": "5 * * * *",
        "integrity_delay": 30,
        "repository": [{"name": "repo-a", "path": "/repos/a"}],
        "manifest_storage": "/units/unit1/manifests",
    }
    # unset fields are omitted entirely
    assert "address" not in data
    assert "sync_delay" not in data
    # discovery stays out of the file while it is not configured
    assert "discovery_port" not in data
    assert "discovery_address" not in data
    # the written file loads back as a valid config
    config = UnitConfig.load(path)
    assert config.port == 8080
    assert [repo.name for repo in config.repository] == ["repo-a"]
    assert config.integrity_delay == 30


def test_quit_without_saving(tmp_path):
    path = tmp_path / "config.yaml"
    wizard, output = run_wizard(path, [field_no("port"), "8080", "q"])
    assert not wizard.saved
    assert wizard.values["port"] == 8080
    assert not path.exists()
    assert "Quit without saving." in output[-1]


def test_invalid_menu_choice_reprompts(tmp_path):
    path = tmp_path / "config.yaml"
    wizard, _ = run_wizard(path, ["99", "x", field_no("port"), "22", "q"])
    assert wizard.values["port"] == 22

# ------------------------------------------------------------- scalar fields


def test_port_validation(tmp_path):
    path = tmp_path / "config.yaml"
    wizard, output = run_wizard(
        path, [field_no("port"), "0", "65536", "abc", "8080", "q"]
    )
    assert wizard.values["port"] == 8080
    joined = "\n".join(output)
    assert "port must be in range 1-65535" in joined
    assert "is not a valid integer" in joined


def test_schedule_validation(tmp_path):
    path = tmp_path / "config.yaml"
    wizard, output = run_wizard(
        path, [field_no("sync_schedule"), "bad", "5 5 * * *", "q"]
    )
    assert wizard.values["sync_schedule"] == "5 5 * * *"
    assert "schedule 'bad' must have exactly 5 fields" in "\n".join(output)


def test_delay_must_be_positive(tmp_path):
    path = tmp_path / "config.yaml"
    wizard, _ = run_wizard(path, [field_no("sync_delay"), "0", "15", "q"])
    assert wizard.values["sync_delay"] == 15


def test_optional_field_cleared_when_empty(tmp_path):
    path = tmp_path / "config.yaml"
    wizard, output = run_wizard(
        path, [field_no("address"), "", "q"], values={"address": "0.0.0.0"}
    )
    assert wizard.values["address"] is None
    assert "address cleared." in "\n".join(output)


def test_required_field_rejects_empty(tmp_path):
    path = tmp_path / "config.yaml"
    wizard, output = run_wizard(
        path, [field_no("identifier"), "", "id-1", "q"]
    )
    assert wizard.values["identifier"] == "id-1"
    assert "required and cannot be empty" in "\n".join(output)


def test_delay_schedule_mutual_exclusion(tmp_path):
    path = tmp_path / "config.yaml"
    wizard, output = run_wizard(
        path,
        [
            field_no("sync_delay"), "20",
            field_no("sync_schedule"), "5 * * * *",
            "q",
        ],
    )
    assert wizard.values["sync_delay"] is None
    assert wizard.values["sync_schedule"] == "5 * * * *"
    assert "cleared the mutually exclusive sync_delay field" in "\n".join(output)


def test_values_property_copies_repository_entries(tmp_path):
    wizard, _ = run_wizard(
        tmp_path / "c.yaml", ["q"],
        values={"repository": [{"name": "a", "path": "/a"}]},
    )
    snapshot = wizard.values
    snapshot["repository"][0]["name"] = "changed"
    assert wizard.values["repository"][0]["name"] == "a"

# ------------------------------------------------------------- discovery fields


def test_discovery_port_validation(tmp_path):
    path = tmp_path / "config.yaml"
    wizard, output = run_wizard(
        path, [field_no("discovery_port"), "0", "65536", "abc", "9100", "q"]
    )
    assert wizard.values["discovery_port"] == 9100
    joined = "\n".join(output)
    assert "port must be in range 1-65535" in joined
    assert "is not a valid integer" in joined


def test_discovery_field_prompts(tmp_path):
    discovery_port = install.MENU_FIELDS[field_index("discovery_port")]
    discovery_address = install.MENU_FIELDS[field_index("discovery_address")]
    wizard, _ = run_wizard(
        tmp_path / "c.yaml",
        ["q"],
        values={"discovery_port": 9100, "discovery_address": "192.168.1.255"},
    )
    # the hints explain that both fields are optional
    assert (
        wizard._field_prompt(discovery_port)
        == "discovery_port (current: 9100) "
        "(1-65535, empty to disable network discovery): "
    )
    assert (
        wizard._field_prompt(discovery_address)
        == "discovery_address (current: 192.168.1.255) "
        "(broadcast address, empty for 255.255.255.255): "
    )


def test_discovery_fields_are_saved(tmp_path):
    path = tmp_path / "config.yaml"
    answers = [
        field_no("discovery_port"), "9100",
        field_no("discovery_address"), "192.168.1.255",
        "s", "q",
    ]
    wizard, _ = run_wizard(path, answers, values=valid_values())
    assert wizard.saved
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["discovery_port"] == 9100
    assert data["discovery_address"] == "192.168.1.255"
    config = UnitConfig.load(path)
    assert config.discovery_port == 9100
    assert config.discovery_address == "192.168.1.255"


def test_clearing_discovery_port_disables_discovery(tmp_path):
    path = tmp_path / "config.yaml"
    values = valid_values()
    values["discovery_port"] = 9100
    values["discovery_address"] = "192.168.1.255"
    answers = [
        field_no("discovery_port"), "",
        field_no("discovery_address"), "",
        "s", "q",
    ]
    wizard, output = run_wizard(path, answers, values=values)
    assert wizard.saved
    assert wizard.values["discovery_port"] is None
    joined = "\n".join(output)
    assert "discovery_port cleared." in joined
    assert "discovery_address cleared." in joined
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert "discovery_port" not in data
    assert "discovery_address" not in data
    # the unit still loads, with network discovery disabled
    assert UnitConfig.load(path).discovery_port is None


def test_existing_discovery_values_are_starting_values(tmp_path, monkeypatch):
    fields = {
        "port": 8090,
        "discovery_port": 9100,
        "discovery_address": "192.168.1.255",
        "identifier": "laptop",
        "trusted_token": "token",
        "sync_delay": 86400,
        "integrity_schedule": "45 23 * * 6",
        "repository": [{"name": "photos", "path": "/data/photos"}],
        "manifest_storage": "/data/manifests",
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(fields), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["install.py", "--config", str(path)])
    output = []
    assert install.main(input_func=lambda _: "q", output_func=output.append) == 0
    joined = "\n".join(output)
    assert "discovery_port     : 9100" in joined
    assert "discovery_address  : 192.168.1.255" in joined
    assert install.load_config_values(path)["discovery_port"] == 9100


def test_menu_lists_repository_entries(tmp_path):
    path = tmp_path / "config.yaml"
    values = {
        "repository": [
            {"name": "photos", "path": "/photos1"},
            {"name": "documents", "path": "/documents"},
        ]
    }
    _, output = run_wizard(path, ["q"], values=values)
    # the entries are listed right below the repository field line
    field_line = next(
        index
        for index, line in enumerate(output)
        if f"{field_no('repository'):>2}. repository" in line
    )
    assert output[field_line].endswith(": 2 entries")
    assert output[field_line + 1] == "    1. photos  ->  /photos1"
    assert output[field_line + 2] == "    2. documents  ->  /documents"


def test_menu_lists_no_entries_for_a_single_unset_repository_field(tmp_path):
    _, output = run_wizard(tmp_path / "config.yaml", ["q"])
    # nothing configured yet: no entry lines, just the field itself
    assert "  ->  " not in "\n".join(output)
    assert "repository         : (not set)" in "\n".join(output)


def test_menu_shows_the_current_entries_after_editing(tmp_path):
    path = tmp_path / "config.yaml"
    answers = [
        field_no("repository"),  # repository menu
        "a", "photos", "/photos1",  # add the first entry
        "q",  # done with the submenu
        field_no("repository"), "a", "documents", "/documents", "q",
        "q",  # quit wizard
    ]
    wizard, output = run_wizard(path, answers)
    # the last menu printed before quitting lists both entries
    field_line = max(
        index
        for index, line in enumerate(output)
        if f"{field_no('repository'):>2}. repository" in line
    )
    assert output[field_line].endswith(": 2 entries")
    assert output[field_line + 1] == "    1. photos  ->  /photos1"
    assert output[field_line + 2] == "    2. documents  ->  /documents"
    assert [entry["name"] for entry in wizard.values["repository"]] == [
        "photos",
        "documents",
    ]


# ------------------------------------------------------------- repository menu


def test_repository_add_edit_delete(tmp_path):
    path = tmp_path / "config.yaml"
    answers = [
        field_no("repository"),  # repository menu
        "a", "repo-a", "/a",  # add entry 1
        "a", "repo-b", "/b",  # add entry 2
        "e", "1", "renamed", "",  # edit entry 1: new name, keep path
        "d", "2", "y",  # delete entry 2
        "q",  # done
        "q",  # quit wizard
    ]
    wizard, output = run_wizard(path, answers)
    assert wizard.values["repository"] == [
        {"name": "renamed", "path": "/a"}
    ]
    joined = "\n".join(output)
    assert "added repository 'repo-a' (/a)" in joined
    assert "updated repository 'renamed' (/a)" in joined
    assert "deleted repository 'repo-b'" in joined


def test_repository_duplicate_name_rejected(tmp_path):
    path = tmp_path / "config.yaml"
    answers = [
        field_no("repository"),
        "a", "dup", "/a",  # first entry
        "a", "dup", "other", "/b",  # "dup" rejected, "other" accepted
        "q", "q",
    ]
    wizard, output = run_wizard(path, answers)
    assert [entry["name"] for entry in wizard.values["repository"]] == [
        "dup",
        "other",
    ]
    assert "repository name 'dup' is already used" in "\n".join(output)


def test_repository_edit_rejects_rename_collision(tmp_path):
    path = tmp_path / "config.yaml"
    answers = [
        field_no("repository"),
        "a", "one", "/a",
        "a", "two", "/b",
        "e", "2", "one", "three", "",  # "one" rejected, "three" accepted
        "q", "q",
    ]
    wizard, _ = run_wizard(path, answers)
    assert [entry["name"] for entry in wizard.values["repository"]] == [
        "one",
        "three",
    ]


def test_repository_delete_without_confirm_keeps_entry(tmp_path):
    path = tmp_path / "config.yaml"
    answers = [field_no("repository"), "d", "1", "n", "q", "q"]
    wizard, output = run_wizard(path, answers)
    assert wizard.values.get("repository") in (None, [])
    assert "kept repository" not in "\n".join(output)


def test_repository_empty_submenu_actions(tmp_path):
    path = tmp_path / "config.yaml"
    # both submenu actions are attempted while the repository menu is open
    answers = [field_no("repository"), "e", "d", "q", "q"]
    wizard, output = run_wizard(path, answers)
    assert wizard.values.get("repository") in (None, [])
    assert "no repository entries to edit" in "\n".join(output)
    assert "no repository entries to delete" in "\n".join(output)


def test_repository_invalid_index(tmp_path):
    path = tmp_path / "config.yaml"
    answers = [field_no("repository"), "a", "a", "/a", "e", "7", "q", "q"]
    wizard, output = run_wizard(path, answers)
    assert wizard.values["repository"] == [{"name": "a", "path": "/a"}]
    assert "invalid entry number" in "\n".join(output)

# ------------------------------------------------------------------- saving


def test_save_refused_when_config_invalid(tmp_path):
    path = tmp_path / "config.yaml"
    # repository + manifest set, but no sync_delay / sync_schedule
    answers = [
        field_no("port"), "8080", field_no("identifier"), "id",
        field_no("trusted_token"), "tok",
        field_no("repository"), "a", "r", "/r", "q",
        field_no("manifest_storage"), "/m", "s", "q",
    ]
    wizard, output = run_wizard(path, answers)
    assert not wizard.saved
    assert not path.exists()
    joined = "\n".join(output)
    assert "config is not valid yet" in joined
    assert "at least one of sync_delay and sync_schedule must be defined" in joined


def valid_values():
    """A fully-valid values dict for the wizard (sync + integrity by delay)."""
    return {
        "port": 9000,
        "identifier": "other",
        "trusted_token": "tok",
        "sync_delay": 60,
        "integrity_delay": 120,
        "repository": [{"name": "r", "path": "/r"}],
        "manifest_storage": "/m",
    }


def test_save_overwrite_cancelled(tmp_path):
    path = valid_yaml(tmp_path)
    original = path.read_text(encoding="utf-8")
    wizard, output = run_wizard(path, ["s", "n", "q"], values=valid_values())
    assert not wizard.saved
    assert path.read_text(encoding="utf-8") == original
    assert "Save cancelled." in "\n".join(output)


def test_save_overwrite_confirmed(tmp_path):
    path = valid_yaml(tmp_path)
    values = valid_values()
    wizard, _ = run_wizard(path, ["s", "y", "q"], values=values)
    assert wizard.saved
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    # the wizard's values replace the old file's contents
    assert data == {
        "port": 9000,
        "identifier": "other",
        "trusted_token": "tok",
        "sync_delay": 60,
        "integrity_delay": 120,
        "repository": [{"name": "r", "path": "/r"}],
        "manifest_storage": "/m",
    }


def test_save_writes_parents(tmp_path):
    path = tmp_path / "nested" / "dirs" / "config.yaml"
    answers = [
        field_no("port"), "1", field_no("identifier"), "i",
        field_no("trusted_token"), "t", field_no("sync_schedule"), "5 * * * *",
        field_no("integrity_delay"), "30",
        field_no("repository"), "a", "r", "/r", "q",
        field_no("manifest_storage"), "/m", "s", "q",
    ]
    wizard, _ = run_wizard(path, answers)
    assert wizard.saved
    assert path.exists()


# ---------------------------------------------------------------------- main


def test_main_loads_existing_config_as_starting_values(
    tmp_path, monkeypatch
):
    path = valid_yaml(tmp_path)
    monkeypatch.setattr(sys, "argv", ["install.py", "--config", str(path)])
    output = []
    exit_code = install.main(
        input_func=lambda _: "q", output_func=output.append
    )
    assert exit_code == 0
    assert "Loaded existing config from" in "\n".join(output)
    # the starting values are visible in the first menu
    assert "8090" in "\n".join(output)


def test_main_fresh_start_when_no_file(tmp_path, monkeypatch):
    path = tmp_path / "nope" / "config.yaml"
    monkeypatch.setattr(sys, "argv", ["install.py", "--config", str(path)])
    output = []
    install.main(input_func=lambda _: "q", output_func=output.append)
    assert "Quit without saving." in "\n".join(output)
    assert "Loaded existing config" not in "\n".join(output)


def test_main_fresh_start_when_file_invalid(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text("port: 1\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["install.py", "--config", str(path)])
    output = []
    install.main(input_func=lambda _: "q", output_func=output.append)
    joined = "\n".join(output)
    assert "is not a valid unit config" in joined
    assert "Loaded existing config" not in joined


def test_main_survives_eof(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    monkeypatch.setattr(sys, "argv", ["install.py", "--config", str(path)])

    def fail_input(prompt):
        raise EOFError

    output = []
    exit_code = install.main(input_func=fail_input, output_func=output.append)
    assert exit_code == 0
    assert "Interrupted." in output


# ------------------------------------------------------------- helpers


def test_format_value():
    port = install.MENU_FIELDS[field_index("port")]
    assert InstallWizard._format_value(port, None) == "(not set)"
    assert InstallWizard._format_value(port, 8080) == "8080"
    assert InstallWizard._format_value(port, 8091) == "8091"  # discovery_port
    repo = install.MENU_FIELDS[field_index("repository")]
    assert InstallWizard._format_value(repo, ["a"]) == "1 entry"
    assert InstallWizard._format_value(repo, ["a", "b"]) == "2 entries"
    delay = install.MENU_FIELDS[field_index("sync_delay")]
    assert InstallWizard._format_value(delay, 30) == "30 s"
    schedule = install.MENU_FIELDS[field_index("sync_schedule")]
    assert InstallWizard._format_value(schedule, "5 * * * *") == '"5 * * * *"'


def test_load_config_values(tmp_path):
    path = valid_yaml(tmp_path)
    values = install.load_config_values(path)
    assert values["port"] == 8090
    assert values["repository"] == [
        {"name": "photos", "path": "/data/photos"}
    ]
    assert install.load_config_values(tmp_path / "missing.yaml") is None
    broken = tmp_path / "broken.yaml"
    broken.write_text("port: 1\n", encoding="utf-8")
    assert install.load_config_values(broken) is None