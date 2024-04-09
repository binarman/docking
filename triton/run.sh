#!/usr/bin/bash

CONT_NAME="alefimov-triton"
IMG_NAME="alefimov-triton-img"

if ! docker start -ai "$CONT_NAME"; then
  docker run -it --network host --device /dev/kfd --device /dev/dri --name "$CONT_NAME" "$IMG_NAME"
fi
