#!/usr/bin/bash

set -e

if [ "$#" -ne 1 ]; then
  echo "Illegal number of parameters"
  exit 1
fi

cd "$1"
added=$(git status --porcelain | grep "^??" | wc -l)
updated=$(git status --porcelain | grep "^ M" | wc -l)
deleted=$(git status --porcelain | grep "^ D" | wc -l)

if [ $added != 0 ] || [ $updated != 0 ] || [ $deleted != 0 ]; then
  git add -A
  git commit -m"added files: ${added}
updated files: ${updated}
deleted files: ${deleted}" -a
fi
