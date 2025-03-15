#!/usr/bin/bash

SCRIPT_DIR=$(dirname "$(realpath -s "$0")")
source "$SCRIPT_DIR/config_files/user.config"

MODELS_PATH="$DEFAULT_MODELS_PATH"

echo "Initializing directory with models"
mkdir -p "$MODELS_PATH"
if [ ! -d "$MODELS_PATH" ]; then
  echo "failed to create directory with models, terminating"
  exit 1
fi
mkdir -p "$MODELS_PATH/stable-diffusion"
mkdir -p "$MODELS_PATH/lora"
mkdir -p "$MODELS_PATH/vae"

wget https://prompthero.com/ai-models/pony-diffusion-v6-xl-download/v6-1-5/file/208b8380-90d2-47dc-86a0-2ef3ec179f0a/download -O "$MODELS_PATH/stable-diffusion/pony_v6.safetensors"
wget https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/resolve/main/sd_xl_base_1.0.safetensors -O "$MODELS_PATH/stable-diffusion/sd_xl_base_1.0.safetensors"
wget 'https://civitai.com/api/download/models/290640?type=Model&format=SafeTensor&size=pruned&fp=fp16' -O pony_v6_xl.safetensors
