#!/bin/bash

POSITIONAL_ARGS=()
INITIALIZE=0
while [[ $# -gt 0 ]]; do
  case $1 in
    -p|--prepare-environment)
      INITIALIZE=1
      shift
      ;;
    *)
      POSITIONAL_ARGS+=("$1") # save positional arg
      shift # past argument
      ;;
  esac
done

trap 'echo "startup.sh received SIGINT or SIGTERM"; pkill -P $$' SIGINT SIGTERM

export GRADIO_ANALYTICS_ENABLED="False"
export DISABLE_TELEMETRY=1
export DO_NOT_TRACK=1
export HF_HUB_DISABLE_IMPLICIT_TOKEN=1
export HF_HUB_DISABLE_TELEMETRY=1

if [[ $INITIALIZE == 0 ]]; then
  export HF_HUB_OFFLINE=1
  export TRANSFORMERS_OFFLINE=1
  echo "starting webui"
  /tools/stable-diffusion-webui/webui.sh -f --listen 2>&1 | tee /tmp/webui.log | grep "Startup time:"&
  echo "starting kohuya_ss"
  source /tools/python_3.10_venv/bin/activate
  /tools/kohya_ss/gui.sh --listen 0.0.0.0 --server_port 7861 --inbrowser &> /tmp/kohuya.log&

  sleep inf

  pkill -g $$
else
  echo "initialize webui environment"
  LAUNCH_SCRIPT="dummy_launcher'" /tools/stable-diffusion-webui/webui.sh -f
  source /tools/stable-diffusion-webui/venv/bin/activate
  pip install --upgrade huggingface_hub
  python /tools/download_hf_models.py

  echo "initialize kohuya_ss environment"
  python3.10 -m venv /tools/python_3.10_venv
  source /tools/python_3.10_venv/bin/activate
  /tools/kohuya_ss/setup.sh
fi


