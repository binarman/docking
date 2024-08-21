#/usr/bin/bash

FULL_REPO_PATH=$(realpath $1)

if [ ! -e "$FULL_REPO_PATH/README.md" ]; then
  git clone https://github.com/triton-lang/triton "$FULL_REPO_PATH"
  cd "$FULL_REPO_PATH"
  git remote rename origin openai
  git remote add binarman https://github.com/binarman/triton
  git fetch binarman
  git remote add rocm https://github.com/ROCm/triton
  git fetch rocm
  pre-commit install
  cd python
  VSCODE_SETTINGS="$FULL_REPO_PATH/.vscode/settings.json"
  mkdir "$FULL_REPO_PATH/.vscode"
  echo "{\"cmake.configureArgs\" : [" > $VSCODE_SETTINGS
  ESCAPED_REPO_PATH=$(echo "$FULL_REPO_PATH" | sed 's/\//\\\//')
  DEBUG=1 strace -f -e trace=execve -s 100 pip3 install -e. 2>&1 >/dev/null |
      grep "\"cmake\", \"$FULL_REPO_PATH\"" |
      sed "s/.*\[\"cmake\", \"$ESCAPED_REPO_PATH\", \(.*\)\].*/\1/" |
      sed 's/", "/",\n  "/g' |
      sed 's/DCMAKE_MAKE_PROGRAM=.*ninja/DCMAKE_MAKE_PROGRAM=ninja/' |
      sed 's/llvm\/llvm-.*include/llvm_persistent\/include/' |
      sed 's/llvm\/llvm-.*lib/llvm_persistent\/lib/' >> $VSCODE_SETTINGS
  echo "] }" >> $VSCODE_SETTINGS
fi

cp -r ~/.triton/llvm/* ~/.triton/llvm_persistent

