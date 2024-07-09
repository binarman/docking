#!/usr/bin/bash
help() {
  echo "Usage: push [-h] [-n] [-m <message>]"
  echo "   -h : show help message"
  echo "   -n : do not perform pre-commit checks"
  echo "   -m : use given string as commit message"
}

PRE_COMMIT=1
COMMIT_MESSAGE="fix"
while getopts ":hnm:" option; do
  case $option in
    h)help; exit;;
    n)PRE_COMMIT=0;;
    m)COMMIT_MESSAGE="$OPTARG";;
    ?)help; exit;;
  esac
done

if ((PRE_COMMIT == 1)); then
  git commit -a -m"$COMMIT_MESSAGE"
else
  git commit -a -n -m"$COMMIT_MESSAGE"
fi
git push binarman $(git branch --show-current)

