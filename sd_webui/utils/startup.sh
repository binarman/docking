#!/bin/bash

export GRADIO_ANALYTICS_ENABLED="False"
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export DISABLE_TELEMETRY=1
export DO_NOT_TRACK=1
export HF_HUB_DISABLE_IMPLICIT_TOKEN=1
export HF_HUB_DISABLE_TELEMETRY=1

trap 'echo "startup.sh received SIGINT or SIGTERM"; pkill -P $$' SIGINT SIGTERM

echo "starting webui"
/tools/stable-diffusion-webui/webui.sh -f --listen 2>&1 | tee /tmp/webui.log | grep "Startup time:"&
echo "starting kohuya_ss"
/tools/kohya_ss/gui.sh --listen 0.0.0.0 --server_port 7861 --inbrowser &> /tmp/kohuya.log&

sleep inf

pkill -g $$
