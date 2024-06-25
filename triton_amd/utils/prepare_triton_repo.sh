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
  VSCODE_SETTINGS="/triton/.vscode/settings.json"
  mkdir /triton/.vscode
  echo "{\"cmake.configureArgs\" : [" > $VSCODE_SETTINGS
  DEBUG=1 strace -f -e trace=execve -s 100 pip3 install -e. 2>&1 >/dev/null | grep '"cmake", "/triton"' | sed "s/.*\[\"cmake\", \"\/triton\", \(.*\)\].*/\1/" >> $VSCODE_SETTINGS
  echo "] }" >> $VSCODE_SETTINGS
fi

cp -r ~/.triton/llvm/* ~/.triton/llvm_persistent

