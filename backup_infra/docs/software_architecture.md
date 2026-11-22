# Software architecture

This document describes the modules of the system and how they interact.

## Modules

There are a few modules used by other tools:

- `manifest.py`: Contains infrastructure for managing the manifest structure and its serialization on disk.
- `unit_config.py`: Contains infrastructure for managing the config for `unit.py`. It includes serialization on disk and parameter validation.
- `network_messages.py`: Contains HTML templates and methods for reporting status to the user, exchanging status with other units, and supporting notification services.

## Network communications

Main network communications are done on top of TCP protocol.
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

This request is designed for unit x unit communication: it transfers the content of one tracked file from a unit that has the current version to a unit that is behind. The synchronization algorithm (see `docs/synchronization.md`) decides *which* files to request; this section defines *how* one file travels.

The request is a GET to the <unit_address>/file address:

```
GET /file?repository=photos&path=vacation/img.jpg HTTP/1.1
x-beacon-token: <trusted_token>
```

- `repository` is the formal repository name (the `name` field of a `repository` config entry);
- `path` is the repository-relative POSIX path of the file, exactly as it appears in the manifest, URL-encoded;
- `x-beacon-token` carries the shared `trusted_token`; a request without it, or with a different token, is refused. Only `file` requests are authenticated this way: the unit hands out user data over them, while `status` and `network` only expose status information.

The hash of the requested file is deliberately **not** part of the request: a unit keeps a single copy of every file, and the requesting unit already knows which version it expects -- it read that hash from the remote manifest returned by the `status` request. Leaving the hash out keeps the request a plain read with no precondition to negotiate.

`path` is canonical when it is not empty, is relative, uses `/` as the only separator, contains no `\`, `:` or NUL character and has no empty, `.` or `..` component. Every other value is rejected, which is what makes a peer supplied path safe to resolve locally; names that need those characters are simply not addressable by this request (they stay tracked in the manifest).

The answer for a file the unit can serve is:

```
HTTP/1.1 200 OK
Content-Type: application/octet-stream
Content-Length: 8123456
X-Beacon-File-Hash: 9f2c1e8a5d4b3c2a1f0e9d8c7b6a5948
X-Beacon-Unit: pc_test1
Connection: close

<raw file bytes>
```

- `X-Beacon-File-Hash` is the hash this unit's manifest records for the file (the md5 hex digest `manifest.py` stores). The receiving unit compares it with the hash it expects *before* reading the body, so a peer that already moved to a newer version costs one round trip and no bytes. The digest of the received bytes is compared with it as well, which catches a damaged transfer and a peer whose file no longer matches its own manifest;
- `X-Beacon-Unit` names the serving unit, which is what the requesting unit reports to the user and to its log.

The response carries no file system metadata. Modification time, owner and access flags of a received file belong to the receiving unit, which runs in a different environment (see the assumptions in the readme): they come from the `fetched_file_mode`, `fetched_owner_id` and `fetched_group_id` config fields and from the process umask. The identity of a file in this system is its content hash, never its metadata.

The status codes of a failed request, with a one line `text/plain` body:

| Code | When |
| --- | --- |
| `400` | `repository` or `path` is missing or ambiguous, or `path` is not canonical |
| `403` | the token is missing or does not match `trusted_token`, or `path` resolves outside the repository root |
| `404` | unknown repository, or the file is not tracked in the manifest |
| `405` | a method other than GET |
| `409` | the repository has `fault` issues, or the file on disk does not match the manifest record (it disappeared, or its modification time moved on): the serving unit's own manifest is stale, so the requester must start again from a fresh `status` request instead of accepting bytes that belong to a version nobody recorded |
| `410` | the manifest records the file as deleted |
| `413` | the file is larger than `max_transfer_size` |
| `415` | the tracked path is not a regular file (symlink, directory, device) |

A `file` request never changes the state of the serving unit: it does not rescan the repository and does not touch a manifest. Serving relies on three independent guards: the file must be tracked in the manifest (which rules out anything the unit does not consider its own data), the resolved path must stay inside the repository root, and the repository must not be faulty. Answering `409` instead of rescanning keeps the integrity check as the only code that writes a manifest.

The body is streamed in both directions and never buffered: the serving unit reads the file in chunks and relies on the flow control of the transport, so a multi gigabyte file costs one chunk of memory, and the `max_parallel_transfers` limit bounds how many transfers a unit serves at the same time.

The receiving unit:

1. writes the incoming bytes next to the target file under the `<target name>.beacon-part` name, which the manifest scan never tracks (see `manifest.TEMP_SUFFIX`); a temporary file that already exists means the same path is being downloaded already;
2. checks that the number of received bytes equals `Content-Length` and that their md5 digest equals the hash expected from the remote manifest;
3. on any mismatch (including a timeout or a peer that disappears mid-transfer) deletes the temporary file and leaves the target untouched, and reports the reason to the synchronization logic, which retries after refreshing the remote manifest;
4. after a successful verification it applies the configured access mode and ownership (`fetched_file_mode`, `fetched_owner_id`, `fetched_group_id`) to the temporary file and renames it onto the target. The rename is atomic within a filesystem, so a crash can never leave a partially written file inside a repository, and a file present in a repository is always a complete version. If the mode or the ownership cannot be applied (a missing privilege, for example), the temporary file is deleted and the fetch is reported as failed rather than publishing a file without the configured properties.

### Discover request

This request is designed for unit x unit communication.

It is used to announce the unit in the network. This request is done over UDP protocol.

### Status request

The status request is designed for unit x unit communication.

The status request is a GET request to the address <unit_address>/status.
The answer to this request should be JSON with a short status (OK, faulty, conflicts) and a list of base64-encoded manifests describing this node.

### Network request

The network request is designed for unit x desktop_notify communication; it provides a short summary for the user's desktop notification.

This is a GET request to the <unit_address>/network endpoint.
The response to this request contains JSON with information about all known units(including current one) in the network:
- Unit name
- Short status: OK, faulty, conflicts
- Time of last synchronization with the current unit

### network_messages.py interfaces

At this stage, `network_messages.py` should implement the following interfaces:

- `generate_summary(unit_identifier: string, uptime_seconds: integer, manifests: list of local manifests, remotes: list of tuples(remote name, remote status, last synchronization time)) -> string` generates the HTML page for the user's `/summary` request;
- `generate_status(manifests: list of manifests) -> string` generates JSON with the unit status and a list of base64-encoded manifests;
- `parse_status(string) -> tuple(status, list of manifests)` decodes the string encoded by generate_status;
- `generate_network_status(remotes: list of tuples(name, status, last sync time)) -> string` generates a JSON string containing the network status;
- `parse_network_status(string) -> list of tuples(name, status, last sync time)` decodes the JSON into the network status;
- `generate_discovery(unit_identifier: string, unit_port: integer, tcp_port: integer, trusted_token: string) -> string` generates the UDP network discovery announcement (unit name, unit UDP port, unit TCP port and trusted token);
- `parse_discovery(string) -> tuple(unit identifier, unit UDP port, unit TCP port, trusted token)` decodes the discovery announcement;
- `canonical_rel_path(string) -> string` validates the `path` of a `file` request and returns it in canonical form (`ValueError` for anything else);
- `parse_file_target(query: dict) -> tuple(repository name, relative path)` decodes the query string of a `file` request (`ValueError` for a missing, ambiguous or non-canonical field);
- `generate_file_headers(unit_identifier: string, file_hash: string, size: integer) -> dict(header name, value)` builds the headers of a successful `file` answer (`Content-Length`, `X-Beacon-File-Hash` and `X-Beacon-Unit`);
- `parse_file_headers(dict headers) -> tuple(file hash, size, unit identifier)` decodes those headers back, accepting any header name casing (`ValueError` for a missing or malformed value).

## desktop_notify.py behavior

`desktop-notify.py` utility makes `network` request to a given address, prepares a multiline message based on response and calls `/usr/bin/notify-send`. If request failed, message should clearly show this, if any of the knowns hosts do not have "OK" status, treat this as a failure.

Failed message uses following argument template: `-t 43200000 -i /usr/custom/scripts/error.png "PROBLEMS DETECTED" "<message body>"`.
Successfull message uses following argument template: `-t 43200000 -i /usr/custom/scripts/ok.png "SYSTEM OP" "<message body>"`

## Network discovery protocol

Every unit works in one of two modes:
- `root`: listens to `discovery_port` UDP and resends broadcast notifications to other known units on the same machine.
- `leaf`: listens to `discovery_port + offset unique to this unit` UDP port.

On startup unit tries to bind with `discovery_port` UDP port, if bind is successful, unit enters `root` mode, otherwise it enters `leaf` mode. In `leaf` mode unit scans ports one by one and tries to bind. It continue until bind is successful or it tries 128 ports. If no free port was found, unit exits with an error message.

After UDP port is bindeded, unit sends broadcast message to the `discovery_address` on `discovery_port` port. This message contains current unit name, unit UDP port, unit TCP port and `trusted_token`.

`root` unit receives messages and resends them to known local units.
Both `root` and `leaf` units update lists of known remote units if received `trusted_token` is equal to current unit `trusted_token`.
For syncronization purposes units on the same different from the current one are considered "remote" units.

A unit is considered to be on the same machine when its announcement arrives from an address of this machine. Because an announcement sent to `discovery_address` carries the address of the interface it was sent from, which is not necessarily the address the hostname of the machine resolves to, the machine is mapped from the hostname addresses, from the source address of the routes used to reach the `discovery_address` network (a routing table lookup only, nothing is sent) and from the address the unit's own announcement comes back through the broadcast.

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