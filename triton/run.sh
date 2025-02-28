#!/usr/bin/bash

SCRIPT_DIR=$(dirname "$(realpath -s "$0")")
source "$SCRIPT_DIR/config_files/user.config"
source "$SCRIPT_DIR/config_files/utilities.sh"

help() {
  echo "Usage: run.sh [-h] [-n <container name>]"
  echo "   -h : show help message"
  echo "   -n : use non default container name"
  echo "   -s : use non default image suffix: amd, nvidia, nogpu"
}

IMAGE_SUFFIX="$(get_device_suffix)"

while getopts ":hn:s:" option; do
  case $option in
    h)help; exit;;
    n)CONT_NAME="$OPTARG";;
    s)IMAGE_SUFFIX="$OPTARG";;
    ?)help; exit;;
  esac
done

if [ -z "$CONT_NAME" ]; then
  mapfile -t EXISTING_CONTAINERS < <(docker container ls -a | grep ${IMAGE_BASE_NAME} | rev | cut -d\  -f 1 | rev)
  if [[ ${#EXISTING_CONTAINERS[@]} == 0 ]]; then
    CONT_NAME="$DEFAUL_CONTAINER_NAME"
  elif [[ ${#EXISTING_CONTAINERS[@]} == 1 ]]; then
    CONT_NAME="${EXISTING_CONTAINERS[0]}"
  else
    select opt in ${EXISTING_CONTAINERS[@]}; do
      if [ $opt ]; then
        CONT_NAME="$opt"
        break
      fi
      echo "please give a number within given limits"
    done
  fi
fi

echo "Running container: $CONT_NAME"

IMAGE_NAME="${IMAGE_BASE_NAME}-${IMAGE_SUFFIX}"

if ! docker start -ai "$CONT_NAME"; then
  if [[ $HAS_AMD_GPU == 1 ]]; then
    docker run -it --network host --device /dev/kfd --device /dev/dri --name "$CONT_NAME" "$IMAGE_NAME"
  elif [[ $HAS_NVIDIA_GPU == 1 ]]; then
    docker run -it --network host --gpus all --name "$CONT_NAME" "$IMAGE_NAME"
  else
    RED='\033[0;31m'
    NC='\033[0m' # No Color
    printf "${RED}RUNNING WITHOUT GPU${NC}\n"
    docker run -it --network host --name "$CONT_NAME" "$IMAGE_NAME"
  fi
fi
