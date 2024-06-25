#!/usr/bin/bash
 
git fetch binarman

DIFF_SIZE="$(git diff | wc -l)"

if [ $DIFF_SIZE -ne 0 ]; then
  git stash
fi

if ! git rebase binarman/$(git branch --show-current); then
  exit 1
fi

if [ $DIFF_SIZE -ne 0 ] && ! git stash pop; then
  exit 1
fi

if [ "$1" = "build" ]
then
  ROOT_DIR=$(git rev-parse --show-toplevel)
  pushd "$ROOT_DIR"
  DEBUG=1 pip3 install -e python
  popd
fi

