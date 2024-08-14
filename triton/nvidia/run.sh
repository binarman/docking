#!/usr/bin/bash

CONT_NAME="alefimov-triton"
IMG_NAME="alefimov-triton-img"

if ! docker start -ai "$CONT_NAME"; then
  docker run -it --gpus all --network host --name "$CONT_NAME" "$IMG_NAME"
fi
