#!/usr/bin/bash

set -e

cd "$1"
added=$(git status --porcelain | grep "^??" | wc -l)
updated=$(git status --porcelain | grep "^ M" | wc -l)
deleted=$(git status --porcelain | grep "^ D" | wc -l)
git add -A
git commit -m"added files: ${added}
updated files: ${updated}
deleted files: ${deleted}" -a
