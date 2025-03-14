#!/usr/bin/bash

SCRIPT_DIR=$(dirname "$(realpath -s "$0")")

source "$SCRIPT_DIR/config_files/user.config"

USER_PASSWORD="$DEFAULT_USER_PASSWORD"
CONTAINER_NAME="$DEFAULT_CONTAINER_NAME"
PA_GUEST_SERVER="/tmp/pulse/native"
PA_GUEST_COOKIE="/home/user/.config/pulse/cookie"
SCREEN_SIZE="$DEFAULT_SCREEN_SIZE"

help() {
  echo "Usage: run.sh [-h] [-p <user password>] [<container name>]"
  echo "   -h : show help message"
  echo "   -p : use non default password for user(default is \"${DEFAULT_USER_PASSWORD}\")"
  echo "   -s : set screen size, default is \"${DEFAULT_SCREEN_SIZE}\")"
}

while getopts ":hp:s:" option; do
  case $option in
    h)help; exit;;
    p)USER_PASSWORD="$OPTARG";;
    s)SCREEN_SIZE="$OPTARG";;
    ?)help; exit;;
  esac
done
shift $((OPTIND-1))
if ! echo "$SCREEN_SIZE" | grep -E '^[0-9]+x[0-9]+$'; then
  echo "wrong format of sceen size, expected format looks like this: 1920x1080"
  exit
fi

if [ "$1" ]; then
  CONTAINER_NAME="$1"
fi


echo "Start container"
if ! docker start "$CONTAINER_NAME"; then
  docker run \
    --detach \
    --init \
    --env PULSE_SERVER=$PA_GUEST_SERVER \
    -v "$XDG_RUNTIME_DIR/pulse/native:$PA_GUEST_SERVER" \
    --env PULSE_COOKIE=$PA_GUEST_COOKIE \
    --name \
    "$CONTAINER_NAME" \
    "$IMAGE_NAME"
fi


echo "Copy pulse audio cookie on guest"
CONTAINER_ADDR=$(docker inspect $CONTAINER_NAME|grep \"IPAddress\"|head -n 1|sed 's/.*: "\(.*\)".*/\1/')
docker cp "$HOME/.config/pulse/cookie" "$CONTAINER_NAME:$PA_GUEST_COOKIE"
sshpass -p "$USER_PASSWORD" ssh -o StrictHostKeyChecking=no "user@$CONTAINER_ADDR" "sudo chown user $PA_GUEST_COOKIE"


echo "Setting up Xephyr on display $GUEST_DISPLAY"
LAST_DISPLAY=$(ls /tmp/.X11-unix| sort | sed "s/X//" | tail -n 1)
GUEST_DISPLAY=":$(echo "$LAST_DISPLAY + 1" | bc)"
Xephyr -screen $SCREEN_SIZE "$GUEST_DISPLAY"&
HOST_DISPLAY="$DISPLAY"
export DISPLAY="$GUEST_DISPLAY"
while ! xset q; do
  echo "waiting for display to start"
  sleep 1
done


echo "Setting up clipboard forwarding between $HOST_DISPLAY and $GUEST_DISPLAY X displays"
function forward_x_selection {
  SRC_D=$1
  DST_D=$2
  SELECTION=$3
  echo "Setting up selection $SELECTION forwarding from $SRC_D to $DST_D display"
  while DISPLAY=$SRC_D $SCRIPT_DIR/clipnotify/clipnotify -s $SELECTION; do
    echo "detected copy from $SRC_D on $SELECTION selection"
    SRC_SEL_DATA=$(xclip -selection $SELECTION -o -display $SRC_D)
    DST_SEL_DATA=$(xclip -selection $SELECTION -o -display $DST_D)
    if [[ "$SRC_SEL_DATA" = "$DST_SEL_DATA" ]]; then
      echo "ignore same selection to prevent backward copying"
    else
      xclip -selection $SELECTION -o -display $SRC_D | xclip -selection $SELECTION -i -display $DST_D
    fi
  done
}

# TODO fix primary selection copy
forward_x_selection "$HOST_DISPLAY" "$GUEST_DISPLAY" clipboard &
#forward_x_selection "$HOST_DISPLAY" "$GUEST_DISPLAY" primary &
forward_x_selection "$GUEST_DISPLAY" "$HOST_DISPLAY" clipboard &
#forward_x_selection "$GUEST_DISPLAY" "$HOST_DISPLAY" primary &


echo "Connecting to container on address $CONTAINER_ADDR"
# remove previous records, because every container run will create new footprint
ssh-keygen -f ~/.ssh/known_hosts -R "$CONTAINER_ADDR"
sshpass -p "$USER_PASSWORD" ssh -o StrictHostKeyChecking=no -X "user@$CONTAINER_ADDR" "export PULSE_SERVER=$PA_GUEST_SERVER; export PULSE_COOKIE=$PA_GUEST_COOKIE; /startup.sh"
