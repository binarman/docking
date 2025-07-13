#!/usr/bin/bash

SCRIPT_DIR=$(dirname "$(realpath -s "$0")")
source "$SCRIPT_DIR/settings.config"

help() {
  echo "Usage: run.sh [-h]"
  echo "   -h : show help message"
}

while getopts ":h" option; do
  case $option in
    h)help; exit;;
    ?)help; exit;;
  esac
done

IMAGE_NAME="${IMAGE_BASE_NAME}"
CONT_NAME="${CONT_BASE_NAME}"

docker run -it --network host -v ~/container_shared:/host --name "$CONT_NAME" "$IMAGE_NAME" bash
