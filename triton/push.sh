#!/usr/bin/bash

if [ -n "$1" ]
then 
   message="$1"
else
   message="fix"
fi

git commit -a -m"$message"
git push binarman $(git branch --show-current)

