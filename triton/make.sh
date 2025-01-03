#!/usr/bin/bash

IMG_NAME="alefimov-triton-img"

HAS_AMD_GPU=$(test -a /dev/kfd && test -a /dev/dri && echo 1)
HAS_NVIDIA_GPU=$(test -a /dev/nvidia0 && echo 1)

SCRIPT_DIR=$(dirname "$(realpath -s "$0")")

if [[ $HAS_AMD_GPU = 1 ]]; then
  docker build -t "$IMG_NAME" -f "$SCRIPT_DIR"/config_files/dockerfile.amd "$SCRIPT_DIR"
elif [[ $HAS_NVIDIA_GPU = 1 ]]; then
  docker build -t "$IMG_NAME" -f "$SCRIPT_DIR"/config_files/dockerfile.nvidia "$SCRIPT_DIR"
else
  docker build -t "$IMG_NAME" -f "$SCRIPT_DIR"/config_files/dockerfile.nogpu "$SCRIPT_DIR"
fi

