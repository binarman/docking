#!/usr/bin/bash

SCRIPT_DIR=$(dirname "$(realpath -s "$0")")
source "$SCRIPT_DIR/config_files/user.config"

help() {
  echo "Usage: run.sh [-h] [-n <container name>]"
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
  HAS_AMD_GPU=$(test -a /dev/kfd && test -a /dev/dri && echo 1)
  HAS_NVIDIA_GPU=$(test -a /dev/nvidia0 && echo 1)
  if [[ $HAS_AMD_GPU = 1 ]]; then
    docker run -it --network host --device /dev/kfd --device /dev/dri -v $HOME/sd-outputs:/stable-diffusion-webui/outputs --name "$CONTAINER_NAME" "$IMAGE_NAME"
  elif [[ $HAS_NVIDIA_GPU = 1 ]]; then
    docker run -it --network host --gpus all -v $HOME/sd-outputs:/stable-diffusion-webui/outputs --name "$CONTAINER_NAME" "$IMAGE_NAME"
  else
    RED='\033[0;31m'
    NC='\033[0m' # No Color
    printf "${RED}RUNNING WITHOUT GPU${NC}\n"
    docker run -it --network host -v $HOME/sd-outputs:/stable-diffusion-webui/outputs --name "$CONTAINER_NAME" "$IMAGE_NAME"
  fi
fi
