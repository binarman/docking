#!/usr/bin/bash

SCRIPT_DIR=$(dirname "$(realpath -s "$0")")
source "$SCRIPT_DIR/settings.config"

help() {
  echo "Usage: make.sh [-h]"
  echo "   -h : show help message"
}

while getopts ":h" option; do
  case $option in
    h)help; exit;;
    ?)help; exit;;
  esac
done

IMAGE_NAME="${IMAGE_BASE_NAME}"

docker build -t "$IMAGE_NAME" -f "$SCRIPT_DIR"/dockerfile "$SCRIPT_DIR"
