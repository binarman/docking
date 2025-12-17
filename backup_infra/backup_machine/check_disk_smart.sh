#!/usr/bin/bash

set -e

if [ $USER != root ]; then
  sudo $0;
else
  error=0
  for disk in /dev/sd?; do
    reallocated=$(smartctl -d sat --all $disk | grep "Reallocated_Event_Count" | tr -s " " | cut -f 10 -d " ")
    if (( $reallocated > 0 )); then
      echo "disk $disk is compromised"
      error=1
    fi
  done
  if (( $error != 0 )); then
    exit 1
  else
    echo "ok"
  fi
fi

