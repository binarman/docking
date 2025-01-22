#!/usr/bin/bash

SCRIPT_DIR=$(dirname "$(realpath -s "$0")")

source "$SCRIPT_DIR/config_files/user.config"

USER_PASSWORD="$DEFAULT_USER_PASSWORD"

help() {
  echo "Usage: make.sh [-h] [-p <user password>]"
  echo "   -h : show help message"
  echo "   -p : use non default password for user(default is \"${DEFAULT_USER_PASSWORD}\")"
}

while getopts ":hn:" option; do
  case $option in
    h)help; exit;;
    p)USER_PASSWORD="$OPTARG";;
    ?)help; exit;;
  esac
done

docker build -t $IMAGE_NAME -f "$SCRIPT_DIR/config_files/dockerfile" --build-arg PASSWORD="$USER_PASSWORD" --build-arg DESKTOP_COLOR="$DESKTOP_COLOR" "$SCRIPT_DIR"
