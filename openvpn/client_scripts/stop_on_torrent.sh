#!/bin/bash
SCRIPTPATH="$( cd -- "$(dirname "$0")" >/dev/null 2>&1 ; pwd -P )"

function notify() {
  local display=":$(ls /tmp/.X11-unix/* | sed 's#/tmp/.X11-unix/X##' | head -n 1)"
  local user=$(who | grep '('$display')' | awk '{print $1}' | head -n 1)
  local uid=$(id -u $user)
  sudo -u $user DISPLAY=$display DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$uid/bus notify-send --icon="${SCRIPTPATH}/warning.png" -t 100000 "$@"
}

while true; do
  if ((ps -A|grep transmission) && (ps -A|grep openvpn)); then
    killall openvpn
    notify "VPN is disconnected because torrent client is working"
  fi
  sleep 1
done

