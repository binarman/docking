This is high level description how unit.py manages manifest syncronization.

## Terminology

- `local manifest`: is a manifest stored on local unit
- `remote manifest`: is a manifest fetched from remote unit
- `N`: no file record in manifest
- `A`: abstract hash
- `B`: abstract hash different from A
- `D`: special token meaning file is deleted

Manifest describes a file history as a sequence of tokens, each token is either a hash of the target file at some point in time or a delete token representing that file on a given path was deleted. History is attached to a path, not file content, file system id or anything like this. This means that if file was moved, history associated with the file should record deletion and new record for new path is created in manifest.

One file could have multiple D tokens in history, it could happen when file was deleted and recreated multiple times.
Each D token contains a unit identifier, where this deletion even was registered.

## Algorithm

local manifest | remote manifest | what to do
--- | --- | ---
 N |   N | Do nothing
 N | A/B | Fetch new file from remote, copy remote file history in local manifest
 N |   D | Copy remote file history in local manifest
 A |   N | Do nothing
 A |   A | Merge remote file history into local one.
 A |   B | If local history is a subset of a remote history(i.e. local is behind remote), fetch new version of file from remote and merge remote into local history. If remote history is a subset of  remote history (i.e. remote is behind local), do nothing (remote will update itself on schedule). If neither of are subset of the other, register a conflict.
 A |   D | If local history is a subset of a remote history (i.e. local is behind remote), delete local copy and merge remote into local history. If remote history is a subset of local history (i.e. remote is behind local), do nothing. If neither are a subset of the other, register a conflict.
 D |   N | Do nothing
 D | A/B | If local history is a subset of a remote history (i.e. local is behind remote), fetch new file from and merge remote into local history. If remote history is a subset of a local history (i.e. remote is behind local), do nothing. If neither are a subset of the other, register a conflict.
 D |   D | Merge remote history into the local one.

Main idea behind this algorithm is to safely keep files and delete of update them only if histories are consistent. If history diverged, request user intervention to investigate the issue manually. Syncronization algorithm itself do not communicate with the user, it is in the scope of higher level logic.

The reason why D token has a unit id is following. Consider local D + remote A/B case, algorithm should not fetch deleted file if it was deleted somewhere in the past on a remote server. Algorithm need to check that remote knows about all local deletion events.

### Checking that one history is a subset of another

Idea is to check that every subset elements is also in "full" history and order is preserved.

Approximate algorithm looks like this:

``` python
def is_subset(subset, full)
    full_pos = 0 # this is a position in full history
    for token in subset:
        while full_pos < len(full_pos) and full[full_pos] != token:
            full_pos += 1
        if full_pos == len(full_pos) or full[full_pos] != token:
            return false
    return true
```

### Merging histories

Idea is to merge two histories in one file, preserve order of events in each individual history. In addition, result should be the same no matter in which order histories are provided to the merging algorithm.

Approximate algorithm looks like this:

``` python
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