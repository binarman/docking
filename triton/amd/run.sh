#!/usr/bin/bash

CONT_NAME="alefimov-triton"
IMG_NAME="alefimov-triton-img"

help() {
  echo "Usage: push [-h] [-n <container name>]"
  echo "   -h : show help message"
  echo "   -n : use non default container name"
}

while getopts ":hn:" option; do
  case $option in
    h)help; exit;;
    n)CONT_NAME="$OPTARG";;
    ?)help; exit;;
  esac
done

if ! docker start -ai "$CONT_NAME"; then
  docker run -it --network host --device /dev/kfd --device /dev/dri --name "$CONT_NAME" "$IMG_NAME"
fi
