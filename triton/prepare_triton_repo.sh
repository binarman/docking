#/usr/bin/bash

if [ ! -e "$1/README.md" ]; then
  git clone https://github.com/openai/triton "$1"
  cd "$1"
  git remote rename origin openai
  git remote add binarman https://github.com/binarman/triton
  git fetch binarman
  pre-commit install
  cd python
  DEBUG=1 pip3 install -e.
fi

