#!/usr/bin/bash

SCRIPT_DIR=$(dirname "$(realpath -s "$0")")
source "$SCRIPT_DIR/config_files/user.config"

help() {
  echo "Usage: run.sh [-h] [-n <container name>]"
  echo "   -h : show help message"
  echo "   -n : use non default container name"
}

CONT_NAME="$DEFAUL_CONTAINER_NAME"

while getopts ":hn:" option; do
  case $option in
    h)help; exit;;
    n)CONT_NAME="$OPTARG";;
    ?)help; exit;;
  esac
done

echo "INITIALIZING ufw firewall"
sudo apt -y install ufw
sudo ufw allow 443
sudo ufw enable

docker run -it --network host --name "$CONT_NAME" "$IMAGE_NAME" --restart unless-stopped
