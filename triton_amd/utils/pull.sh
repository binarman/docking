#!/usr/bin/bash
 
git fetch binarman
git stash
if ! git rebase binarman/$(git branch --show-current); then
  exit 1
fi
if ! git stash pop; then
  exit 1
fi

if [ "$1" = "build" ]
then
  ROOT_DIR=$(git rev-parse --show-toplevel)
  pushd "$ROOT_DIR"
  DEBUG=1 pip3 install -e python
  popd
fi

