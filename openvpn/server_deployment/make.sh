#!/usr/bin/bash

SCRIPT_DIR=$(dirname "$(realpath -s "$0")")

source "$SCRIPT_DIR/config_files/user.config"

help() {
  echo "Usage: make.sh [server ip address]"
  echo
  echo "Availabe interfaces are:"
  ip address|grep -E ': |inet'
}

if [ -z $1 ]; then
  help
  exit
fi

SERVER_IP_ADDR=$1

docker build -t "$IMAGE_NAME" -f "$SCRIPT_DIR"/dockerfile "$SCRIPT_DIR" --build-arg CA_PASSPHRASE="$CA_PASSPHRASE" --build-arg SERVER_IP_ADDR="$SERVER_IP_ADDR"
