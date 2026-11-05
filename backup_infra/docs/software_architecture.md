## Modules

There are few modules used by other tools:

- `manifest.py`: Contains infrastructure for managing manifest structure and it's serialization on disk.
- `unit_config.py`: Contains infrastructure for managing config for `unit.py`. It includes serialization on disk and parameter verification.
- `network_messages.py`: Contains html templates and methods to report status to the user, exchange status with other units and notification services

## Network communications

There are three types of network communications in the system:

1. unit x user (in web browser): `summary` and `reevaluate` requests
2. unit x unit: `status` request
3. unit x desktop_notify: `network` request

`network_messages.py` is the one point which defines format for network communication between units.
It defines functions that format outgoing and parse incoming network messages. This includes as status requests/answers, user-faced web pages, network discovery messages.
At this point focus is on status requests and web page, it does not 

### Summary request

Web page is designed to show user system summary and provides a handle to start internal reevaluation faulty repositories status.
Request from user looks like simple GET request on address <unit_address>/summary
Server answers with a web page which contains summary for current unit:

- Name of the unit
- System uptime
- Which repositories are managed by this unit, their statuses (OK, faulty, have conflicts), if there are conflicts, this summary should show which files are affected and what unit caused these conflicts.
- If repository is faulty, page should have a button "Reevaluate integrity". If user press it, POST request is sent to the unit to /reevaluate endpoint.
- List of other known units and a short summary: OK, faulty(if at least one repo is faulty there), conflicts(if at least one repo has conflicts), time of last synchronization.

### Reevaluate request

POST request for reevaluation is awaited on <unit_address>/reevaluate address.

### Status request

Status request is designed for unit x unit communications.

Status request is a GET request on address <unit_address>/status.
Answer to this request should be a json with a short status(OK, faulty, conflicts) and a list of manifests describing this node.

### Network request

Network requests is designed for unit x desktop_notify communications it provides a short summary for user desktop notification.

This is a GET request to <unit_address>/network endpoint.
Response to this request contains json with information about all known units in the network:
- Unit name
- Short status: OK, faulty, conflicts
- Last time of synchronization with current unit

### network_messages.py interfaces

At this stage `network_messages.py` should implement following interfaces:

- `generate_summary(manifests: list of manifests, remotes: list of tuples(remote name, remote status, last syncronization time)) -> string` generates html page for user `/summary` request;
- `generate_status(manifests: list of manifests) -> string` generates string containing base64 encoded manifests;
- `parse_status(string) -> list of manifests` decodes base64 manifest to a list of manifests received from the remote unit;
- `generate_network_status(remotes: list of tuples(name, status, last sync time)) -> string` generates json string containing encoded network status;
- `parse_network_status(string) -> list of tuples(name, status, last sync time)` decodes json to network status.

## Dependencies

### install.py

Depends on `unit_config.py`.

### unit.py

Depends on `manifest.py`, `unit_config.py`, `network_messages.py`.

### desktop_notify.py

Depends on `manifest.py`, `network_messages.py`.

### create_manifest.py

Depends on `manifest.py`.

### show_manifest.py

Depends on `manifest.py`.

### compare_manifests.py

Depends on `manifest.py`.