# Beacon

A file backup system with built-in integrity monitoring.

## Why it exists

Plain backups only help if the copies are actually intact and the disks
holding them are still alive. This system was built after a few incidents
with failing disks, so it has a self-monitoring layer on top of the
backups itself: it not only copies files between hosts, it periodically
verifies that the files and the disks are still healthy, so that a
degrading disk is noticed before data is lost.

## Terminology

| Term | Meaning |
|---|---|
| **host** | A physical server. |
| **unit** | A Docker container on a host that runs the backup scripts. One or more units run per host. A unit is *not* a continuously running service: it wakes up on a schedule to sync, and on a separate schedule to check integrity and sits idle otherwise. |
| **repository** (repo) | A named directory of managed files, owned by a unit and synchronized between hosts. A repository is the basic unit of sync and verification. |
| **manifest** | It describes the repository's contents. Manifect contains version, repository location, repository name, information about tracked files with checksums and timestamps, so integrity can be verified at any time. |

## System layout

Here is the example of system layout in local network

```
Local network
|-- host-1: physical or virtual server
|   `-- unit-1: Docker container
|     |-- /data/repositories/
|     |  |-- photos/
|     |  |-- documents/
|     `-- /data/manifests/
|         |-- photos.manifest
|         `-- documents.manifest
`-- host-2
    |-- unit-1
    `-- unit-2
```

## Operating principles

- **Roughtly daily** each unit wakes up and syncs its repositories with peer hosts.
- **When new unit is enabled** it discovers other hosts in local network and syncronize with them. This includes first start and wake up after booting system.
- **Weekly / monthly** each unit verifies file integrity against its manifests and checks disk health.
- **A silent unit is a signal** a unit that misses a scheduled sync or check for N days is worth investigating, even if nothing reported an error.
- **Designated units sends scheduled signals to user** Some units could be configured to send status email or show desktop notifications to user periodically.

## High level description of unit

TBD

## High level description of manifest

TBD

## Network discovery protocol

TBD