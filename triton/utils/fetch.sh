#!/usr/bin/bash

for remote in $(git remote); do
  git fetch $remote;
done

