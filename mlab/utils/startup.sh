#!/bin/bash

if (( $1 == "debug" )); then
  bash
  exit
fi

trap 'echo "startup.sh received SIGINT or SIGTERM"; pkill -P $$' SIGINT SIGTERM

export GRADIO_ANALYTICS_ENABLED="False"
export DISABLE_TELEMETRY=1
export DO_NOT_TRACK=1
export HF_HUB_DISABLE_IMPLICIT_TOKEN=1
export HF_HUB_DISABLE_TELEMETRY=1

export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
python /tools/control_services.py
pkill -g $$

