#!/usr/bin/bash

SCRIPT_DIR=$(dirname "$(realpath -s "$0")")
source "$SCRIPT_DIR/config_files/user.config"

help() {
  echo "Usage: run.sh [-h] [-n <container name>] <client name>"
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

shift $((OPTIND-1))

CLIENT_NAME=$1

docker exec "$CONT_NAME" "/utils/add_client.sh" "${CLIENT_NAME}"
