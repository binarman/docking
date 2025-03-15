#!/usr/bin/bash

SCRIPT_DIR=$(dirname "$(realpath -s "$0")")
source "$SCRIPT_DIR/config_files/user.config"

CONTAINER_NAME="$DEFAULT_CONTAINER_NAME"
MODELS_PATH="$DEFAULT_MODELS_PATH"
OUTPUT_PATH="$DEFAULT_OUTPUT_PATH"

help() {
  echo "Usage: run.sh [-h] [-n <container name>]"
  echo "   -h : show help message"
  echo "   -m : set path to directory with models"
  echo "   -n : use non default container name"
  echo "   -o : set path to directory with outputs"
}

while getopts ":hn:" option; do
  case $option in
    h)help; exit;;
    m)MODELS_PATH="$OPTARG";;
    n)CONTAINER_NAME="$OPTARG";;
    o)OUTPUT_PATH="$OPTARG";;
    ?)help; exit;;
  esac
done

echo "Initializing directory with models"
mkdir -p "$MODELS_PATH"
if [ ! -d "$MODELS_PATH" ]; then
  echo "failed to create directory with models, terminating"
  exit 1
fi
mkdir -p "$MODELS_PATH/stable-diffusion"
mkdir -p "$MODELS_PATH/lora"
mkdir -p "$MODELS_PATH/vae"

echo "Running $CONTAINER_NAME container"
if ! docker start -ai "$CONTAINER_NAME"; then
  HAS_AMD_GPU=$(test -a /dev/kfd && test -a /dev/dri && echo 1)
  HAS_NVIDIA_GPU=$(test -a /dev/nvidia0 && echo 1)
  if [[ $HAS_AMD_GPU = 1 ]]; then
    docker run -it --network host --device /dev/kfd --device /dev/dri -v "$OUTPUT_PATH":/stable-diffusion-webui/outputs -v "$MODELS_PATH":/models --name "$CONTAINER_NAME" "$IMAGE_NAME"
  elif [[ $HAS_NVIDIA_GPU = 1 ]]; then
    docker run -it --network host --gpus all -v "$OUTPUT_PATH":/stable-diffusion-webui/outputs -v "$MODELS_PATH":/models --name "$CONTAINER_NAME" "$IMAGE_NAME"
  else
    RED='\033[0;31m'
    NC='\033[0m' # No Color
    printf "${RED}RUNNING WITHOUT GPU${NC}\n"
    docker run -it --network host -v "$OUTPUT_PATH":/stable-diffusion-webui/outputs -v "$MODELS_PATH":/models --name "$CONTAINER_NAME" "$IMAGE_NAME"
  fi
fi
