#!/usr/bin/env python3
"""Interactive wizard for configuring a Beacon unit.

The wizard produces the unit's YAML config file (see the "Config file"
section of the project readme and ``scripts/unit_config.py``).  The main
menu mirrors the config fields; picking a field prompts for its value.
The ``repository`` field has its own submenu where entries can be
added, edited or deleted.

Network discovery is configured by the optional ``discovery_port`` and
``discovery_address`` fields: leaving ``discovery_port`` empty keeps
network discovery disabled (the unit then only serves its own status
endpoints), and leaving ``discovery_address`` empty makes the unit
announce to the limited broadcast address (255.255.255.255).

Every answer is validated before it is accepted, and the config is
checked with ``UnitConfig`` again before writing, so the produced file
always loads back as a valid unit config.  If the target file already
holds a valid config it is loaded as the starting point.

Usage:
    install.py [--config FILE]
"""

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

from unit_config import UnitConfig, validate_schedule


@dataclass(frozen=True)
class Field:
    """One entry of the wizard menu, mirroring a unit config field."""

    key: str  # config key / UnitConfig argument name
    kind: str  # "port" | "required_string" | "optional_string" | "delay" | "schedule" | "repository"


MENU_FIELDS = (
    Field("port", "port"),
    Field("discovery_port", "port"),
    Field("discovery_address", "optional_string"),
    Field("address", "optional_string"),
    Field("identifier", "required_string"),
    Field("trusted_token", "required_string"),
    Field("sync_delay", "delay"),
    Field("sync_schedule", "schedule"),
    Field("integrity_delay", "delay"),
    Field("integrity_schedule", "schedule"),
    Field("repository", "repository"),
    Field("manifest_storage", "required_string"),
)

# Fields that are mutually exclusive with the field under that key.
_EXCLUSIVE_PARTNERS = {
    "sync_delay": "sync_schedule",
    "sync_schedule": "sync_delay",
    "integrity_delay": "integrity_schedule",
    "integrity_schedule": "integrity_delay",
}

# Prompt hints for each field kind.
_PROMPT_HINTS = {
    "port": " (1-65535, empty to clear)",
    "required_string": "",
    "optional_string": " (empty to clear)",
    "delay": " (seconds, empty to clear)",
    "schedule": " (minute hour day month weekday, empty to clear)",
}

# Hints that replace the hint of the field kind for a single field.
_PROMPT_HINTS_BY_KEY = {
    "discovery_port": " (1-65535, empty to disable network discovery)",
    "discovery_address": " (broadcast address, empty for 255.255.255.255)",
}


class InstallWizard:
    """Menu driven editor for a unit config."""

    def __init__(self, config_path, values=None, input_func=None, output_func=None):
        self._config_path = str(config_path)
        self._values = dict(values or {})
        self._input = input_func if input_func is not None else input
        self._output = output_func if output_func is not None else print
        self._saved = False

    @property
    def saved(self):
        """True once the config was written to disk at least once."""
        return self._saved

    @property
    def values(self):
        """Copy of the current config values (list entries are copied too)."""
        values = dict(self._values)
        if isinstance(values.get("repository"), list):
            values["repository"] = [dict(entry) for entry in values["repository"]]
        return values

    def run(self):
        """Run the wizard until the user quits."""
        while True:
            self._print_menu()
            choice = self._input("Choose an option: ").strip().lower()
            if choice in ("q", "quit", "exit"):
                self._say("Goodbye." if self._saved else "Quit without saving.")
                return
            if choice in ("s", "save"):
                self._save()
                continue
            if not choice.isdigit() or not 1 <= int(choice) <= len(MENU_FIELDS):
                self._fail(f"invalid choice: {choice!r}")
                continue
            field = MENU_FIELDS[int(choice) - 1]
            if field.kind == "repository":
                self._edit_repositories()
            else:
                self._edit_scalar(field)

    def _print_menu(self):
        self._say("Beacon unit configuration")
        for index, field in enumerate(MENU_FIELDS, start=1):
            value = self._values.get(field.key)
            self._say(f"  {index:>2}. {field.key:<18} : {self._format_value(field, value)}")
            if field.kind == "repository" and value:
                for entry_index, entry in enumerate(value, start=1):
                    self._say(
                        self._format_repository_entry(entry_index, entry, "    ")
                    )
        self._say(f"   s) save config to {self._config_path}")
        self._say("   q) quit")

    @staticmethod
    def _format_repository_entry(index, entry, indent="  "):
        """One repository line, e.g. ``  1. photos  ->  /data/photos``."""
        return f"{indent}{index}. {entry['name']}  ->  {entry['path']}"

    @staticmethod
    def _format_value(field, value):
        if value is None:
            return "(not set)"
        if field.kind == "repository":
            count = len(value)
            return f"{count} {'entry' if count == 1 else 'entries'}"
        if field.kind == "delay":
            return f"{value} s"
        if field.kind == "schedule":
            return f'"{value}"'
        return str(value)

    def _edit_scalar(self, field):
        while True:
            raw = self._input(self._field_prompt(field)).strip()
            if not raw:
                if field.kind == "required_string":
                    self._fail(f"{field.key} is required and cannot be empty")
                    continue
                value = None
            else:
                value = self._parse_scalar(field, raw)
                if value is None:
                    continue
            self._apply_field(field, value)
            return

    def _field_prompt(self, field):
        value = self._values.get(field.key)
        current = "" if value is None else f" (current: {self._format_value(field, value)})"
        return f"{field.key}{current}{self._prompt_hint(field)}: "

    @staticmethod
    def _prompt_hint(field):
        """The prompt hint of a field: its own override or the kind's hint."""
        return _PROMPT_HINTS_BY_KEY.get(field.key, _PROMPT_HINTS[field.kind])

    def _parse_scalar(self, field, raw):
        """Validate ``raw`` for a scalar field; return None if invalid."""
        kind = field.kind
        if kind in ("port", "delay"):
            if not raw.isdigit():
                self._fail(f"{raw!r} is not a valid integer")
                return None
            value = int(raw)
            if kind == "port" and not 1 <= value <= 65535:
                self._fail(f"port must be in range 1-65535, got {value}")
                return None
            if kind == "delay" and value <= 0:
                self._fail(f"delay must be a positive integer, got {value}")
                return None
            return value
        if kind == "schedule":
            try:
                validate_schedule(raw)
            except ValueError as error:
                self._fail(str(error))
                return None
            return raw
        return raw

    def _apply_field(self, field, value):
        self._values[field.key] = value
        partner = _EXCLUSIVE_PARTNERS.get(field.key)
        if value is not None and partner is not None and self._values.get(partner) is not None:
            self._values[partner] = None
            self._say(f"Note: cleared the mutually exclusive {partner} field.")
        if value is None:
            self._say(f"{field.key} cleared.")
        else:
            self._say(f"{field.key} = {self._format_value(field, value)}")

    def _edit_repositories(self):
        repos = list(self._values.get("repository") or [])
        while True:
            self._print_repositories(repos)
            choice = self._input("Choose an option: ").strip().lower()
            if choice in ("q", "done", "quit"):
                self._values["repository"] = repos
                return
            if choice in ("a", "add"):
                repos.append(self._prompt_repository_entry(repos))
                entry = repos[-1]
                self._say(f"added repository '{entry['name']}' ({entry['path']})")
            elif choice in ("e", "edit"):
                self._edit_repository_entry(repos)
            elif choice in ("d", "del", "delete"):
                self._delete_repository_entry(repos)
            else:
                self._fail(f"invalid choice: {choice!r}")

    def _print_repositories(self, repos):
        self._say("Repository entries:")
        if not repos:
            self._say("  (none)")
        for index, entry in enumerate(repos, start=1):
            self._say(self._format_repository_entry(index, entry))
        self._say("")
        self._say("  a) add entry")
        self._say("  e) edit entry")
        self._say("  d) delete entry")
        self._say("  q) done")

    def _prompt_repository_entry(self, repos):
        """Prompt for name and path, re-asking until both are valid."""
        names = {entry["name"] for entry in repos}
        while True:
            name = self._input("Repository name: ").strip()
            if not name:
                self._fail("repository name must not be empty")
            elif name in names:
                self._fail(f"repository name {name!r} is already used")
            else:
                break
        while True:
            path = self._input("Repository path: ").strip()
            if not path:
                self._fail("repository path must not be empty")
            else:
                break
        return {"name": name, "path": path}

    def _edit_repository_entry(self, repos):
        if not repos:
            self._fail("there are no repository entries to edit")
            return
        index = self._prompt_index(len(repos))
        if index is None:
            return
        entry = repos[index]
        while True:
            raw = self._input(f"New name for '{entry['name']}' (empty to keep): ").strip()
            if not raw:
                break
            other_names = {other["name"] for other in repos if other is not entry}
            if raw in other_names:
                self._fail(f"repository name {raw!r} is already used")
                continue
            entry["name"] = raw
            break
        while True:
            raw = self._input(f"New path for '{entry['name']}' (empty to keep '{entry['path']}'): ").strip()
            if not raw:
                break
            entry["path"] = raw
            break
        self._say(f"updated repository '{entry['name']}' ({entry['path']})")

    def _delete_repository_entry(self, repos):
        if not repos:
            self._fail("there are no repository entries to delete")
            return
        index = self._prompt_index(len(repos))
        if index is None:
            return
        entry = repos[index]
        answer = self._input(f"Delete repository '{entry['name']}' (y/n)? ")
        if answer.strip().lower() in ("y", "yes"):
            repos.pop(index)
            self._say(f"deleted repository '{entry['name']}'")
        else:
            self._say(f"kept repository '{entry['name']}'")

    def _prompt_index(self, count):
        raw = self._input("Entry number: ").strip()
        if not raw.isdigit() or not 1 <= int(raw) <= count:
            self._fail(f"invalid entry number: {raw!r}")
            return None
        return int(raw) - 1

    def _save(self):
        try:
            config = self._build_config()
        except ValueError as error:
            self._fail(f"config is not valid yet: {error}")
            self._say("Set or fix the fields, then save again.")
            return
        path = Path(self._config_path)
        if path.exists():
            answer = self._input(f"Overwrite existing file {path} (y/n)? ")
            if answer.strip().lower() not in ("y", "yes"):
                self._say("Save cancelled.")
                return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                yaml.safe_dump(config_to_yaml_dict(config), sort_keys=False),
                encoding="utf-8",
            )
        except OSError as error:
            self._fail(f"cannot write {path}: {error}")
            return
        try:
            UnitConfig.load(path)
        except (ValueError, OSError, yaml.YAMLError) as error:
            self._fail(f"saved file failed validation: {error}")
            return
        self._saved = True
        self._say(f"Unit config saved to {path}")
        self._say(repr(config))

    def _build_config(self):
        kwargs = {
            field.key: self._values[field.key]
            for field in MENU_FIELDS
            if self._values.get(field.key) is not None
        }
        return UnitConfig(**kwargs)

    def _say(self, message=""):
        self._output(message)

    def _fail(self, message):
        self._output(f"error: {message}")


def config_to_yaml_dict(config):
    """A valid config as an ordered plain dict suitable for YAML dumping."""
    data = {}
    for field in MENU_FIELDS:
        value = getattr(config, field.key)
        if value is None:
            continue
        if field.kind == "repository":
            data[field.key] = [
                {"name": repo.name, "path": repo.path} for repo in value
            ]
        else:
            data[field.key] = value
    return data


def load_config_values(path):
    """Values dict for an existing valid config at ``path``, else ``None``."""
    try:
        config = UnitConfig.load(path)
    except (OSError, ValueError, yaml.YAMLError):
        return None
    return config_to_yaml_dict(config)


def main(input_func=None, output_func=None):
    parser = argparse.ArgumentParser(
        description="Interactive wizard that configures a Beacon unit."
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="unit config file to create (default: config.yaml)",
    )
    args = parser.parse_args()

    say = output_func if output_func is not None else print
    values = load_config_values(args.config)
    if values is not None:
        say(f"Loaded existing config from {args.config} as starting values.")
    elif Path(args.config).exists():
        say(f"{args.config} exists but is not a valid unit config; starting fresh.")

    wizard = InstallWizard(
        config_path=args.config,
        values=values,
        input_func=input_func,
        output_func=output_func,
    )
    try:
        wizard.run()
    except (KeyboardInterrupt, EOFError):
        say("Interrupted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())