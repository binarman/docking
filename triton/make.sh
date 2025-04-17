#!/usr/bin/bash

SCRIPT_DIR=$(dirname "$(realpath -s "$0")")

source "$SCRIPT_DIR/config_files/user.config"
source "$SCRIPT_DIR/config_files/utilities.sh"

IMAGE_SUFFIX="$(get_device_suffix)"

help() {
  echo "Usage: make.sh [-h] [-s <override image suffix>]"
  echo "   -h : show help message"
  echo "   -s : use specific image type: amd, amd_pytorch, nvidia, nogpu"
}

while getopts ":hs:" option; do
  case $option in
    h)help; exit;;
    s)IMAGE_SUFFIX="$OPTARG";;
    ?)help; exit;;
  esac
done

IMAGE_NAME="${IMAGE_BASE_NAME}-${IMAGE_SUFFIX}"
IMAGE_TAG="$(date +'%S%M%H-%d-%m-%Y')"

docker build -t "$IMAGE_NAME:$IMAGE_TAG" -f "$SCRIPT_DIR"/config_files/dockerfile.${IMAGE_SUFFIX} --build-arg USER_NAME="$USER_NAME" --build-arg USER_EMAIL="$USER_EMAIL" --build-arg INITIAL_TRITON_BRANCH="$INITIAL_TRITON_BRANCH" "$SCRIPT_DIR"

docker image tag "$IMAGE_NAME:$IMAGE_TAG" "$IMAGE_NAME:latest"

