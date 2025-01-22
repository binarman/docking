#!/usr/bin/bash

SCRIPT_DIR=$(dirname "$(realpath -s "$0")")

source "$SCRIPT_DIR/config_files/user.config"

USER_PASSWORD="$DEFAULT_USER_PASSWORD"
CONTAINER_NAME="$DEFAULT_CONTAINER_NAME"

help() {
  echo "Usage: run.sh [-h] [-p <user password>] [<container name>]"
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

if [ "$1" ]; then
  CONTAINER_NAME="$1"
fi

if ! docker start "$CONTAINER_NAME"; then
  docker run --detach --init --name "$CONTAINER_NAME" "$IMAGE_NAME"
fi

CONTAINER_ADDR=$(docker inspect $CONTAINER_NAME|grep \"IPAddress\"|head -n 1|sed 's/.*: "\(.*\)".*/\1/')

LAST_DISPLAY=$(ls /tmp/.X11-unix| sort | sed "s/X//" | tail -n 1)
NEW_DISPLAY=$(echo "$LAST_DISPLAY + 1" | bc)

echo "opening display $NEW_DISPLAY"
Xephyr -screen 1920x1080 ":$NEW_DISPLAY"&
export DISPLAY=":$NEW_DISPLAY"
while ! xset q; do
  echo "waiting for display to start"
  sleep 1
done
# remove previous records, because every container run will create new footprint
ssh-keygen -f ~/.ssh/known_hosts -R "$CONTAINER_ADDR"
sshpass -p "$USER_PASSWORD" ssh -o StrictHostKeyChecking=no -X "user@$CONTAINER_ADDR" "/startup.sh"
