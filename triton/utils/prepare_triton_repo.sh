#!/usr/bin/bash

# import environment varaibles, like MAX_JOBS
source ~/.bashrc

help() {
  echo "Usage: prepare_triton_repo.sh [-h] [-s] <path>"
  echo "     -h        : show help message"
  echo "     -s        : skip build if python/build dir exists"
  echo "     -b <name> : name of branch to checkout before build"
  echo "     <path>    : path where to fetch and build triton"
}

SKIP=0
BRANCH_NAME=""
while getopts ":hsb:" option; do
  case $option in
    h)help; exit;;
    s)SKIP=1;;
    b)BRANCH_NAME="$OPTARG";;
    ?)help; exit;;
  esac
done

shift $((OPTIND-1))

if [ -z $1 ]; then
  help
  exit
fi

FULL_REPO_PATH=$(realpath $1)

if [ ! -e "$FULL_REPO_PATH/README.md" ]; then
  git clone https://github.com/triton-lang/triton "$FULL_REPO_PATH"
  cd "$FULL_REPO_PATH"
  if [[ "$BRANCH_NAME" ]]; then
    git checkout "$BRANCH_NAME"
  fi
  git remote rename origin openai
  git remote add binarman https://github.com/binarman/triton
  git fetch binarman
  git remote add rocm https://github.com/ROCm/triton
  git fetch rocm
  pre-commit install
fi

cd "$FULL_REPO_PATH/python"

if [ -a build ] && [[ $SKIP = 1 ]]; then
  exit
fi

VSCODE_SETTINGS="$FULL_REPO_PATH/.vscode/settings.json"
mkdir "$FULL_REPO_PATH/.vscode"
echo "{\"cmake.configureArgs\" : [" > $VSCODE_SETTINGS
ESCAPED_REPO_PATH=$(echo "$FULL_REPO_PATH" | sed 's/\//\\\//')
DEBUG=1 strace -f -e trace=execve -s 1000 pip3 install -e. --no-build-isolation 2>&1 >/dev/null |
    grep "\"cmake\", \"$FULL_REPO_PATH\"" |
    sed "s/.*\[\"cmake\", \"$ESCAPED_REPO_PATH\", \(.*\)\].*/\1/" |
    sed 's/", "/",\n  "/g' |
    sed 's/DCMAKE_MAKE_PROGRAM=.*ninja/DCMAKE_MAKE_PROGRAM=ninja/' |
    sed 's/DPYBIND11_INCLUDE_DIR=.*include/DPYBIND11_INCLUDE_DIR=\/root\/.triton\/pybind11\/pybind11-2.13.1\/include\//' |
    sed 's/llvm\/llvm-.*include/llvm\/llvm-ubuntu-x64\/include/' |
    sed 's/llvm\/llvm-.*lib/llvm\/llvm-ubuntu-x64\/lib/' >> $VSCODE_SETTINGS
echo "] }" >> $VSCODE_SETTINGS
