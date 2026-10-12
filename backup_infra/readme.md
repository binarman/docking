# Beacon

Beacon is a file backup system project with built-in integrity monitoring for home use.

## Why it exists

Plain backups only help if the copies are actually intact and the disks
holding them are still alive. This system was built after a few incidents
with failing disks, so it has a self-monitoring layer on top of the
backups itself: it not only copies files between hosts, it periodically
verifies that the files and the disks are still healthy, so that a
degrading disk is noticed before data is lost.

This system is designed with the following assumptions in mind:

- Most tracked files do not change; changes are rare;
- System tracks personal photo archives, documents, etc. Files are rarely (on average, less often than once per day) added or deleted;
- System runs on a consumer-grade hardware with relatively low reliability;
- System runs in trusted local network, no encryption is needed(pending, may be reevaluated);
- System should expect file changes from a user, because some data copies are directly accessible by user;
- Asking the user to accept changes does not add too much overhead;
- System is fully functional when at least two different units(see definition below) are online;
- Even with only one system online, system should perform self checks and report issues to the user.

## Terminology

| Term | Meaning |
|---|---|
| **host** | A physical or virtual server. |
| **unit** | A Docker container on a host that runs the backup scripts. One or more units run per host. A unit is *not* a continuously running service: it wakes up on a schedule to sync, and on a separate schedule to check integrity and sits idle otherwise. |
| **repository** (repo) | A named directory of managed files, owned by a unit and synchronized between hosts. A repository is the basic unit of sync and verification. |
| **manifest** | Describes the repository's contents. Manifest contains version, repository location, repository name, information about tracked files with checksums and timestamps, so integrity can be verified at any time. |

## Example use case

There are three machines on the local network:

- PC, works most of the time
- Laptop, offline most of the time, turned on periodically
- Home server used as a file share, works all the time

And two sets of data to backup:
- photos
- documents

User decides documents are more important, so there will be three copies of documents and two copies of photos:

- PC: photos, documents
- Laptop: documents
- Server: photos, documents

Every midnight data copies are synchronized, when laptop boots up it tries to fetch changes from other copies.
PC and laptop periodically shows desktop notifications to the user with short status of backup network.

## System layout

Here is an example of the system layout on the local network

```
Local network
|-- host-1: PC
|   `-- unit
|       |-- /data/repositories/
|       |   |-- photos/
|       |   |-- documents/
|       `-- /data/manifests/
|           |-- photos.manifest
|           `-- documents.manifest
|-- host-2: Laptop
|   `-- unit
|       |-- /data/repositories/
|       |   |-- documents/
|       `-- /data/manifests/
|           `-- documents.manifest
`-- host-3: Server
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
- **Sync on startup**. Unit discovers other units on the local network and synchronize with them as soon as it goes online. This includes both first start and wake up after container restarts.
- **Weekly / monthly integrity checks**. Each unit verifies file integrity against its manifests and checks disk health.
- **A silent unit is a signal**. A unit that misses a scheduled sync or check for N days is worth investigating, even if nothing reported an error.
- **Units send signals to user**. A unit can be configured to show a desktop notification to a user or send status email periodically, even if there are no issues.

## High-level description of a unit

Unit is a docker container that runs script unit.py.

Internal state of the unit includes:
- Local manifests, one for each repository
- Faulty marker, set true if at least one issues in any local manifest is "fault"
- List of other units in network with state summary: is faulty, functional repos, repos with issues, last update time

unit.py script parses given config file, loads local manifests for configured repos, then runs network discovery and then spawns three async tasks:
- listens and answer appropriately to incoming network connections
  - network discovery requests
  - liveness requests
  - requests for repo manifests
  - send tracked file upon request
  - request to resolve given issue. This removes an issue for a given file and forces information recomputation for it(hash, time stamp, etc.)
  - request for overall system summary and issue resolution page. Issue resolution page provides a fast interface to mark issues resolved and confirmation for file deletion.
- synchronize with other units
  - checks liveness of other units
  - ask other units to share manifests for the same repos this unit manage
  - fetch new and changed files from other units
  - remove files marked by user as deleted
  - update local manifests
- periodically check file integrity
  - Collect hashes of tracked files, if there are io errors, add "fault" record in the list of issues, mark current unit as faulty
  - Compare new hashes with previous, if old and new hashes for a file differ, add record in the list of issues

This script is configured by yaml config, see description below.

### Config file

Config file contains following fields:

- **port**: Network port to listen
- **address**: Optional address to listen
- **sync_delay**: Optional integer that defines delay in seconds between synchronizations
- **sync_schedule**: Optional string that defines schedule of synchronization in format "YYYY MM DD hh mm ss w", each field could be *, which means event should run regardless of this date part.
  YYYY represents year, MM - month, DD - day of month, hh - hour of a day, mm - minute in a hour, ss - second in a minute, w - day of a week, starting from 0 - Monday.
  For example "* * * 00 11 22 *" - synchronization should start every day of every month of every year at at 00:11:22, every day of the week
- **integrity_delay**: Optional integer that defines delay in seconds between integrity checks
- **integrity_schedule**: defines the schedule of integrity checks, in the same format as sync_schedule
- **repository**: Array of repositories to track. Each repository includes following fields:
  - **name**: formal name of repository, used to sync between units
  - **path**: local path in filesystem to the root of this repository

"sync_delay" and "sync_schedule" are mutually exclusive fields, but at least one of them should be defined.
Same applies to "integrity_delay" and "integrity_schedule" pair.

Example:

```yaml
port: 8090
sync_delay: 86400
integrity_schedule: "* * 03 00 11 22 *"
repository:
  - name: photos
    path: /data/photos
  - name: documents
    path: /data/documents
```

## High-level description of manifest

Manifest is an in-memory object or a pickle file stored on disk describing status of a repository.
Serialized manifest file is not part of a repository, it is located outside of all repositories.
It includes following information:

- **version**: integer, manifest format version. This document defines version 1
- **path**: PosixPath, absolute path to local repository root directory, which was used to generate manifest. Manifests on different units could have different paths for the same repository.
- **name**: string, repository name. Same on different units for the same repository.
- **files**: dict, maps file hashes to a list of file descriptions. Each file description contains:
  - **path**: PosixPath, file path relative to repository root
  - **timestamp**: datetime, last modification timestamp
- **issues**: list, list of encountered problems: io errors, unexpected hash changes, etc. Each issue description is an object:
  - **type**: string: contains "fault" or "hash"
  - **path**: PosixPath: path to a file reading which created this issue
  - **description**: string: text description to display to user

## Network discovery protocol

TBD

## Executables

### install.py

This is a wizard that deploys unit on a local machine.

### create_manifest.py

TBD

### show_manifest.py

TBD

### compare_manifests.py

TBD

### unit.py

TBD

### desktop-notify.py

This utility requests system status from a given node and sends desktop notification via notify-send.
Optionally added to cron by installation wizard.

## Open issues

- What if same file is modified in two copies and then synced. How to detect such situation?
- What if user takes one of machines outside of trusted perimiter? For example laptop, need to disable syncronization or implement authentification+encryption mechanisms