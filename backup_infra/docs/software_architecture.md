# Software architecture

This document describes the modules of the system and how they interact.

## Modules

There are a few modules used by other tools:

- `manifest.py`: Contains infrastructure for managing the manifest structure and its serialization on disk.
- `unit_config.py`: Contains infrastructure for managing the config for `unit.py`. It includes serialization on disk and parameter validation.
- `network_messages.py`: Contains HTML templates and methods for reporting status to the user, exchanging status with other units, and supporting notification services.

## Network communications

There are multiple types of network communications in the system:

1. unit x user (in web browser): `summary`, `reevaluate` requests
2. unit x unit: `status`, `file` and `discover` requests
3. unit x desktop_notify: `network` request

`network_messages.py` is the single point that defines the format of network communication between units.
It defines functions that format outgoing and parse incoming network messages. This includes status request/answer formatting, user-facing web pages, and network discovery messages.

### Summary request

The web page is designed to show the user a system summary and provides a way to start internal reevaluation of faulty repositories.
The request from the user is a simple GET request to the address <unit_address>/summary
The server answers with a web page that contains the summary for the current unit:

- Name of the unit
- System uptime
- Which repositories are managed by this unit and their statuses (OK, faulty, have conflicts). If there are conflicts, the summary should show which files are affected and which unit caused them.
- If a repository is faulty, the page should have a "Reevaluate integrity" button. If the user presses it, a POST request is sent to the /reevaluate endpoint.
- A list of other known units and a short summary: OK, faulty (if at least one repo is faulty there), conflicts (if at least one repo has conflicts), time of last synchronization.

### Reevaluate request

The unit awaits POST requests for reevaluation at the <unit_address>/reevaluate address.

### File request

GET request for a tracked file. Detailed description TBD.

### Discover request

Request to announce the unit in the network. Detailed description TBD.

### Status request

The status request is designed for unit x unit communication.

The status request is a GET request to the address <unit_address>/status.
The answer to this request should be JSON with a short status (OK, faulty, conflicts) and a list of base64-encoded manifests describing this node.

### Network request

The network request is designed for unit x desktop_notify communication; it provides a short summary for the user's desktop notification.

This is a GET request to the <unit_address>/network endpoint.
The response to this request contains JSON with information about all known units in the network:
- Unit name
- Short status: OK, faulty, conflicts
- Time of last synchronization with the current unit

### network_messages.py interfaces

At this stage, `network_messages.py` should implement the following interfaces:

- `generate_summary(unit_identifier: string, uptime_seconds: integer, manifests: list of local manifests, remotes: list of tuples(remote name, remote status, last synchronization time)) -> string` generates the HTML page for the user's `/summary` request;
- `generate_status(manifests: list of manifests) -> string` generates JSON with the unit status and a list of base64-encoded manifests;
- `parse_status(string) -> tuple(status, list of manifests)` decodes the string encoded by generate_status;
- `generate_network_status(remotes: list of tuples(name, status, last sync time)) -> string` generates a JSON string containing the network status;
- `parse_network_status(string) -> list of tuples(name, status, last sync time)` decodes the JSON into the network status.
- The `discover` and `file` related handlers are TBD

## Dependencies

### install.py

Depends on `unit_config.py`.

### unit.py

Depends on `manifest.py`, `unit_config.py`, `network_messages.py`.

### desktop_notify.py

Depends on `network_messages.py`.

### create_manifest.py

Depends on `manifest.py`.

### show_manifest.py

Depends on `manifest.py`.

### compare_manifests.py

Depends on `manifest.py`.