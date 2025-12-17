#/bin/bash
if [ $# -ne 2 ]
then
  echo "requires 2 arguments: <from> <to>"
  exit 1
fi

set -e

SERVER=$(cut -f 2 -d @ <<< "$2" | cut -f 1 -d :)
DST_LOCATION=$(cut -f 2 -d : <<< "$2")

ssh "$SERVER" test -d "$DST_LOCATION"

rsync -e "ssh -o 'BatchMode yes'" -r --update --delete --exclude=.git $1 $2

