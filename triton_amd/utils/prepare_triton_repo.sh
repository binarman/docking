#/usr/bin/bash

if [ ! -e "$1/README.md" ]; then
  git clone https://github.com/triton-lang/triton "$1"
  cd "$1"
  git remote rename origin openai
  git remote add binarman https://github.com/binarman/triton
  git fetch binarman
  git remote add rocm https://github.com/ROCm/triton
  git fetch rocm
  pre-commit install
  cd python
  VSCODE_SETTINGS="$1/.vscode/settings.json"
  mkdir "$1/.vscode"
  echo "{\"cmake.configureArgs\" : [" > $VSCODE_SETTINGS
  ESCAPED_PATH=$(echo "$1" | sed 's/\//\\\//')
  DEBUG=1 strace -f -e trace=execve -s 100 pip3 install -e. 2>&1 >/dev/null | grep "\"cmake\", \"$1\"" | sed "s/.*\[\"cmake\", \"$ESCAPED_PATH\", \(.*\)\].*/\1/" >> $VSCODE_SETTINGS
  echo "] }" >> $VSCODE_SETTINGS
fi

cp -r ~/.triton/llvm/* ~/.triton/llvm_persistent

