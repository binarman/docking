#!/usr/bin/bash

SCRIPT_DIR=$(dirname "$(realpath -s "$0")")

source "$SCRIPT_DIR/config_files/user.config"

HAS_AMD_GPU=$(test -a /dev/kfd && test -a /dev/dri && echo 1)
HAS_NVIDIA_GPU=$(test -a /dev/nvidia0 && echo 1)

if [[ $HAS_AMD_GPU = 1 ]]; then
  IMG_SUFFIX="amd"
elif [[ $HAS_NVIDIA_GPU = 1 ]]; then
  IMG_SUFFIX="nvidia"
else
  IMG_SUFFIX="nogpu"
fi

docker build -t "$IMAGE_NAME" -f "$SCRIPT_DIR"/config_files/dockerfile.${IMG_SUFFIX} "$SCRIPT_DIR"
