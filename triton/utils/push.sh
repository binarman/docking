#!/usr/bin/bash
help() {
  echo "Usage: push [-h] [-n] [-m <message>]"
  echo "   -h : show help message"
  echo "   -n : do not perform pre-commit checks"
  echo "   -f : do a force push"
  echo "   -m : use given string as commit message"
}

PRE_COMMIT=1
COMMIT_MESSAGE="fix"
FORCE_PUSH=0
while getopts ":hnfm:" option; do
  case $option in
    h)help; exit;;
    n)PRE_COMMIT=0;;
    f)FORCE_PUSH=1;;
    m)COMMIT_MESSAGE="$OPTARG";;
    ?)help; exit;;
  esac
done

if ((PRE_COMMIT == 1)); then
  if ! git commit -a -m"$COMMIT_MESSAGE"; then
    git commit -a -m"$COMMIT_MESSAGE"
  fi
else
  git commit -a -n -m"$COMMIT_MESSAGE"
fi

if ((FORCE_PUSH == 1)); then
  git push -f binarman $(git branch --show-current)
else
  git push binarman $(git branch --show-current)
fi

