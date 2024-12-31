#!/usr/bin/bash

CONT_NAME="tuning-triton"
IMG_NAME="tuning-triton-img"

docker container rm $CONT_NAME
docker image rm $IMG_NAME
