This is a high-level description of how unit.py manages manifest synchronization.

## Terminology

- `local manifest`: a manifest stored on the local unit
- `remote manifest`: a manifest fetched from a remote unit
- `N`: no file record in the manifest
- `A`: an abstract hash
- `B`: an abstract hash different from A
- `D`: a special token meaning the file is deleted

The manifest describes a file history as a sequence of tokens; each token is either a hash of the target file at some point in time or a delete token representing that the file on a given path was deleted. The history is attached to a path, not to the file content, file system ID, or anything like this. This means that if a file was moved, the history associated with the file records a deletion and a new record for the new path is created in the manifest.

One file could have multiple `D` tokens in its history; this could happen when the file was deleted and recreated multiple times.
Each `D` token contains the unit identifier where the deletion event was registered.

## Algorithm

Before syncronization with remote, repo is checked for `fault` issues. If there are any, do not synchronize, because these errors could be a sign of hardware of software failers. Same happens on remote repository. If remote has `fault` issues it should refuse providing manifest for faulty repo.
`conflict` issues related to currently processed remote are removed and reevaluated in the process of syncronization.
`conflict` issues related to other remotes are left intact.

| Local manifest | Remote manifest | What to do |
| --- | --- | --- |
| `N` | `N` | Do nothing |
| `N` | `A/B` | Fetch the new file from the remote unit and copy the remote file history into the local manifest |
| `N` | `D` | Copy the remote file history into the local manifest |
| `A` | `N` | Do nothing |
| `A` | `A` | Merge the remote file history into the local one |
| `A` | `B` | If the local history is a subset of the remote history (i.e., local is behind remote), fetch the new version of the file from the remote unit and merge the remote history into the local one. If the remote history is a subset of the local history (i.e., remote is behind local), do nothing (the remote unit will update itself on schedule). If neither is a subset of the other, register a conflict |
| `A` | `D` | If the local history is a subset of the remote history (i.e., local is behind remote), delete the local copy and merge the remote history into the local one. If the remote history is a subset of the local history (i.e., remote is behind local), do nothing. If neither is a subset of the other, register a conflict |
| `D` | `N` | Do nothing |
| `D` | `A/B` | If the local history is a subset of the remote history (i.e., local is behind remote), fetch the new file from the remote unit and merge the remote history into the local one. If the remote history is a subset of the local history (i.e., remote is behind local), do nothing. If neither is a subset of the other, register a conflict |
| `D` | `D` | Merge the remote history into the local one |

The main idea behind this algorithm is to keep files safe and to delete or update them only if the histories are consistent. If the histories diverged, request user intervention to investigate the issue manually. The synchronization algorithm itself does not communicate with the user; that is in the scope of the higher-level logic.

"Fetch the file from the remote unit" is a `file` request; its format and the rules of writing the received bytes into the repository are described in the "File request" section of `docs/software_architecture.md`. Fetching is only one of the actions of the table above: the fetched version is recorded with the merged history, so a file and a manifest for it are always stored together.

The file system metadata of a received file is the one the receiving unit assigns, not the one of the sender. The manifest record of a file that was just fetched therefore uses the moment the file was stored locally as its `timestamp`, which keeps "the file is newer than its record" a reliable sign that a manifest no longer describes its repository (the `409` case of a `file` request).

The reason why the `D` token has a unit ID is as follows. Consider the local `D` + remote `A/B` case: the algorithm should not fetch a deleted file if it was deleted somewhere in the past on the remote server. The algorithm needs to check that the remote unit knows about all local deletion events.

### Checking that one history is a subset of another

The idea is to check that every element of the subset is also in the "full" history and that the order is preserved.

An approximate algorithm looks like this:

```python
def is_subset(subset, full):
    full_pos = 0  # this is a position in the full history
    for token in subset:
        while full_pos < len(full) and full[full_pos] != token:
            full_pos += 1
        if full_pos == len(full):  # algorithm reached the end of the full history, but did not find the token
            return False
    return True
```

### Merging histories

The idea is to merge two histories into one, preserving the order of events in each individual history. In addition, the result should be the same no matter in which order the histories are provided to the merging algorithm.

An approximate algorithm looks like this:

```python
def merge_history(h1, h2):
    m = []
    h1_pos = 0
    h2_pos = 0
    while h1_pos < len(h1) or h2_pos < len(h2):
        if h1_pos < len(h1) and h2_pos == len(h2):
            m += [h1[h1_pos]]
            h1_pos += 1
            continue
        if h2_pos < len(h2) and h1_pos == len(h1):
            m += [h2[h2_pos]]
            h2_pos += 1
            continue
        if h1[h1_pos] == h2[h2_pos]:
            m += [h1[h1_pos]]
            h1_pos += 1
            h2_pos += 1
        elif h1[h1_pos] < h2[h2_pos]:
            m += [h1[h1_pos]]
            h1_pos += 1
        else:
            m += [h2[h2_pos]]
            h2_pos += 1
    return m
```