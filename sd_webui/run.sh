#!/usr/bin/bash

CONT_NAME="alefimov-sd-webui"
IMG_NAME="alefimov-sd-webui-img"

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
  if test -a /dev/kfd && test -a /dev/dri; then
    docker run -it --network host --device /dev/kfd --device /dev/dri -v $HOME/sd-outputs:/stable-diffusion-webui/outputs --name "$CONT_NAME" "$IMG_NAME"
  else
    RED='\033[0;31m'
    NC='\033[0m' # No Color
    printf "${RED}RUNNING WITHOUT GPU${NC}\n"
    docker run -it --network host -v $HOME/sd-outputs:/stable-diffusion-webui/outputs --name "$CONT_NAME" "$IMG_NAME"
  fi
fi
