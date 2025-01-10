#!/usr/bin/bash

SCRIPT_DIR=$(dirname "$(realpath -s "$0")")

source "$SCRIPT_DIR/config_files/user.config"

help() {
  echo "Usage: make.sh <ca passphrase> <server ip address>"
}

if [ -z $1 ] || [ -z $2 ]; then
  help
  exit
fi

CA_PASSPHRASE=$1

SERVER_IP_ADDR=$2

docker build -t "$IMAGE_NAME" -f "$SCRIPT_DIR"/dockerfile "$SCRIPT_DIR" --build-arg CA_PASSPHRASE="$CA_PASSPHRASE" --build-arg SERVER_IP_ADDR="$SERVER_IP_ADDR"
