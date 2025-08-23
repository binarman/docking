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

if ! which ufw; then
  echo "INSTALLING ufw firewall"
  sudo apt -y install ufw
fi

if ! sudo ufw status | grep 443 | grep ALLOW; then
  echo "INITIALIZING firewall"
  sudo ufw allow 443
  sudo ufw enable
fi

if ! docker start "$CONT_NAME"; then
  echo "Starting new container"
  docker run -d --network host --name "$CONT_NAME" --cap-add=NET_ADMIN --restart unless-stopped "$IMAGE_NAME" /bin/sh -c "/utils/startup.sh"
fi
