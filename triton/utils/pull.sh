#!/usr/bin/bash

REMOTE_NAME="binarman"
 
help() {
  echo "Usage: pull [-h] [-f] [-v] <command>"
  echo "     -h : show help message"
  echo "     -f : do reset --hard instead of rebase"
  echo "     -v : verbose mode"
  echo "command : possible values are empty or \"build\""
}

VERBOSE=0
FORCE=0
while getopts ":hfv" option; do
  case $option in
    h)help; exit;;
    f)FORCE=1;;
    v)VERBOSE=1;;
    ?)help; exit;;
  esac
done

shift $((OPTIND-1))

git fetch "$REMOTE_NAME"

DIFF_SIZE="$(git diff | wc -l)"

if [ $FORCE -ne 0 ]; then
  git reset --hard "$REMOTE_NAME"/$(git branch --show-current)
else
  if [ $DIFF_SIZE -ne 0 ]; then
    git stash
  fi

  if ! git rebase "$REMOTE_NAME"/$(git branch --show-current); then
    exit 1
  fi

  if [ $DIFF_SIZE -ne 0 ] && ! git stash pop; then
    exit 1
  fi
fi

if [ "$1" = "build" ]
then
  ROOT_DIR=$(git rev-parse --show-toplevel)
  pushd "$ROOT_DIR"
  if [ $VERBOSE -eq 1 ]; then
    DEBUG=1 pip3 install --verbose -e python --no-build-isolation
  else
    DEBUG=1 pip3 install -e python --no-build-isolation
  fi
  popd
fi

