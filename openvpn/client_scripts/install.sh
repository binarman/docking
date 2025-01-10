#!/bin/bash

if [[ $(id -u) -ne 0 ]]; then
  echo "script should be run as a root"
  exit 1
fi

INSTALLATION_PATH="/usr/custom_bin"

service vpn_guard stop

mkdir -p /usr/custom_bin/
cp warning.png "${INSTALLATION_PATH}"
cp stop_on_torrent.sh "${INSTALLATION_PATH}"
cp vpn_guard.service /etc/systemd/system/
service vpn_guard start
