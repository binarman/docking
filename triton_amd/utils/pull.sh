#!/usr/bin/bash
 
help() {
  echo "Usage: push [-h] [-v] <command>"
  echo "     -h : show help message"
  echo "     -v : verbose mode"
  echo "command : possible values are empty or \"build\""
}

VERBOSE=0
while getopts ":hv" option; do
  case $option in
    h)help; exit;;
    v)VERBOSE=1;;
    ?)help; exit;;
  esac
done

shift $((OPTIND-1))

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
  if [ $VERBOSE -eq 1 ]; then
    DEBUG=1 pip3 install --verbose -e python
  else
    DEBUG=1 pip3 install -e python
  fi
  popd
fi

