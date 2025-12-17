#!/usr/bin/bash

set -e

if [ $USER != root ]; then
  sudo $0;
else
  smartctl -t long $1
  smartctl -t offline $1
  #smartctl -x $1
fi
