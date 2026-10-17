This is high level descriptoin how unit.py manages syncronization.

## Terminology

- `local manifest`: is a manifest stored on local unit
- `remote manifest`: is a manifest fetched from remote unit
- `N`: no file record in manifest
- `A`: abstrace hash
- `B`: abstract different from A hash
- `D`: special token meaning file is deleted

## Algorithm

local manifest | remote manifest | what to do
--- | --- | ---
 N |   N | do nothing
 N | A/B | fetch new file from remote
 N |   D | do nothing
 A |   N | do nothing
 A |   A | do nothing
 A |   B | if remote contains A hash in history, fetch new version of file from remote, otherwise add this file to the list of conflicts in local manifest
 A |   D | if remote contains A hash in history, delete local copy. If there are no A hash on remote, add this file to the list of conflicts
 D |   N | do nothing
 D | A/B | if remote manifest contains D hash identical to local one in history, fetch new file from remote. Otherwise add conflict in manifest
 D |   D | if D tokens are equal do nothing, else if remote D token is lexicographically less, add it to the local file hash history
