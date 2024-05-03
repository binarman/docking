#/usr/bin/bash

if [ ! -e "$1/README.md" ]; then
  git clone https://github.com/openai/triton "$1"
  cd "$1"
  git remote rename origin openai
  git remote add binarman https://github.com/binarman/triton
  git fetch binarman
  pre-commit install
  cd python
  PATH=/utils:$PATH CMAKE_ARG_DUMP="/triton/.vscode/settings.json" DEBUG=1 /usr/bin/pip3 install -e.
fi

