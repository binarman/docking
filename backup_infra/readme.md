# Beacon

Beacon is a file backup system with built-in integrity monitoring for home use.

## Why it exists

Plain backups only help if the copies are actually intact and the disks
holding them are still alive. This system was built after a few incidents
with failing disks, so it adds a self-monitoring layer on top of the
backups: it not only copies files between hosts, but also periodically
verifies that the files and the disks are still healthy, so that a
degrading disk is noticed before data is lost.

This system is designed with the following assumptions in mind:

- Most tracked files do not change; changes are rare;
- The system tracks personal photo archives, documents, etc. Files are rarely added or deleted (on average, less often than once per day);
- The system runs on consumer-grade hardware with relatively low reliability;
- The system runs on a trusted local network, so no encryption is needed (pending, may be reevaluated);
- The system should expect file changes from the user, because some data copies are directly accessible to the user;
- Asking the user to accept changes does not add too much overhead;

## Terminology

| Term | Meaning |
|---|---|
| **host** | A physical or virtual server. |
| **unit** | A Docker container on a host that runs the backup scripts. One or more units run per host. A unit is *not* a continuously running service: it wakes up on a schedule to sync, on a separate schedule to check integrity, and sits idle otherwise. |
| **repository** (repo) | A named directory of managed files, owned by a unit and synchronized between hosts. A repository is the basic unit of sync and verification. |
| **manifest** | Describes the repository's contents. The manifest contains the version, root path, repository name, metadata and information about tracked files with checksums and timestamps. |
| **tracked file** | A file stored in one of the repositories; i.e., all files in all repositories are considered tracked. |

## Example use case

There are three machines on the local network:

- PC, works most of the time
- Laptop, offline most of the time, turned on periodically
- Home server used as a file share, works all the time

And two sets of data to backup:
- photos
- documents

The user decides documents are more important, so there will be three copies of documents and two copies of photos:

- PC: photos, documents
- Laptop: documents
- Server: photos, documents

Every midnight, data copies are synchronized; when the laptop boots up, it tries to fetch changes from the other copies.
The PC and the laptop periodically show desktop notifications to the user, with a short status of the backup network.

## System layout

Each host can run multiple units. In practice, this could be useful for setting different synchronization schedules for different repos or for storing one repo on physically different storage devices.

Here is an example of the system layout on the local network:

```
Local network
|-- host: PC
|   `-- unit
|       |-- /data/repositories/
|       |   |-- photos/
|       |   |-- documents/
|       `-- /data/manifests/
|           |-- photos.manifest
|           `-- documents.manifest
|-- host: Laptop
|   `-- unit
|       |-- /data/repositories/
|       |   |-- documents/
|       `-- /data/manifests/
|           `-- documents.manifest
`-- host: Server
    |-- unit
    |   |-- /data/repositories/
    |   |   `-- documents/
    |   `-- /data/manifests/
    |       `-- documents.manifest
    `-- unit
        |-- /data/repositories/
        |   `-- photos/
        `-- /data/manifests/
            `-- photos.manifest
```

## Operating principles

- **Daily syncs**. Each unit wakes up and syncs its repositories with peer hosts.
- **Sync on startup**. The unit discovers other units on the local network and synchronizes with them as soon as it goes online. This includes both the first start and wake-ups after container restarts.
- **Weekly / monthly integrity checks**. Each unit verifies file integrity against its manifests and checks disk health.
- **A silent unit is a signal**. A unit that misses a scheduled sync or check for N days is worth investigating, even if nothing reported an error.
- **Units send signals to the user**. A unit can be configured to show a desktop notification to the user or send a status email periodically, even if there are no issues.
- **Single unit is also functional**. Even with only one unit online, the system should perform self-checks and report issues to the user.

## High-level description of a unit

A unit is a Docker container that runs the script unit.py.

The internal state of the unit includes:
- Local manifests, one for each repository
- A faulty marker, set to true if at least one issue in any local manifest is "fault"
- A list of other units in the network, with a state summary (is faulty, functional repos, repos with issues, last update time)

The unit.py script parses a given config file, loads local manifests for the configured repos, runs network discovery, and then spawns three async tasks:
- listens for and answers appropriately to incoming network connections
  - network discovery requests
  - liveness requests
  - requests for repo manifests
  - requests to send tracked files
  - requests to resolve a given issue. This removes the issue for the given file and forces information to be recomputed for it (hash, timestamp, etc.)
  - requests for the overall system summary and an issue resolution page. The issue resolution page provides a fast interface to mark issues as resolved and confirm file deletion.
- synchronizes with other units
  - checks the liveness of other units
  - asks other units to share manifests for the same repos this unit manages
  - fetches new and changed files from other units; does not use data from faulty units or remote repos with unexpected hash changes
  - removes files marked as deleted by the user
  - updates local manifests
- periodically checks file integrity
  - collects hashes of tracked files; if there are I/O errors, adds a "fault" record to the list of issues and marks the current unit as faulty
  - compares new hashes with the previous ones; if the old and new hashes for a file differ, adds a record to the list of issues

The script is configured by a YAML config file; see the description below.

### Config file

The config file contains the following fields:

- **port**: Network port to listen on.
- **address**: Optional address to listen on.
- **unit identifier**: This could be considered the name of the unit. It is expected to be unique in the backup network.
- **trusted_token**: Arbitrary string that is used to verify that the unit is communicating with a compatible network of units. This token is sent back by the units in response to discovery requests. If the sent token is equal to the one in the config file, the unit is considered a valid peer.
- **sync_delay**: Optional integer that defines the delay in seconds between synchronizations.
- **sync_schedule**: Optional string that defines the schedule of synchronization, in cron schedule format:
```
    * * * * *
  # | | | | |
  # | | | | day of the week (0–6) (Sunday to Saturday)
  # | | | month (1–12)
  # | | day of the month (1–31)
  # | hour (0–23)
  # minute (0–59)
```
  For example, "45 23 * * 6" — synchronization should start at 23:45 (11:45 PM) every Saturday.
- **integrity_delay**: Optional integer that defines the delay in seconds between integrity checks.
- **integrity_schedule**: Defines the schedule of integrity checks, in the same format as sync_schedule.
- **repository**: Array of repositories to track. Each repository includes the following fields:
  - **name**: Formal name of the repository, used to sync between units.
  - **path**: Local path in the filesystem to the root of this repository.
- **manifest_storage**: Local path to the directory where manifest files are stored.

"sync_delay" and "sync_schedule" are mutually exclusive fields, but at least one of them should be defined.
The same applies to the "integrity_delay" and "integrity_schedule" pair.

Example:

```yaml
port: 8090
address: 0.0.0.0
trusted_token: "Hello my backup network"
sync_delay: 86400
integrity_schedule: "* * 03 00 11 22 *"
repository:
  - name: photos
    path: /data/photos
  - name: documents
    path: /data/documents
```

## High-level description of manifest

The manifest is an in-memory object or a pickle file stored on disk, describing the status of a repository.
The serialized manifest file is not part of a repository; it is located outside of all repositories.
It includes the following information:

- **version**: integer, manifest format version. This document defines version 1.
- **path**: PosixPath, absolute path to the local repository root directory that was used to generate the manifest. Manifests on different units could have different paths for the same repository.
- **name**: string, repository name. The same on different units for the same repository.
- **files**: dict, maps file paths to file metadata. Each entry contains:
  - **history**: list of strings, the history of changes for a given file. If the file was added or modified, the related record contains a string in the format "hash:<file hash>"; if the file was deleted, the string contains "deleted:<unit identifier>".
  - **timestamp**: datetime, last modification timestamp.
- **issues**: list, list of encountered problems (I/O errors, unexpected hash changes, conflicts, etc.). Each issue description is an object:
  - **type**: string, contains one of "fault" or "conflict".
  - **path**: PosixPath, path to the file whose reading created this issue.
  - **remote**: string or None, unit identifier of the remote unit with which the conflict occurred.
  - **description**: string, text description to display to the user; may contain more details. Empty content is possible.

## Network discovery protocol

TBD

## Core tools

### install.py

This is a wizard that deploys a unit on a local machine.

### unit.py

This is the core utility in the backup network.

### desktop-notify.py

This utility requests a system status from a given unit and sends a desktop notification via notify-send.
Optionally added to cron by the installation wizard.

## Debug and test tools

These tools are used for debugging purposes and for easy review of manifest contents.

### create_manifest.py

Creates a new manifest from a given directory.

### show_manifest.py

Prints the contents of a given manifest file in a human-readable form.

### compare_manifests.py

Compares two manifests and prints common files and differences, i.e., unique files for each manifest and common files with different hashes or statuses.

## Open issues

- What if the user takes one of the machines outside of the trusted perimeter (for example, the laptop)? Need to implement authentication and encryption mechanisms.
- How to discover multiple units on one host? They cannot use the same network port.
- How and when to trim file history?
