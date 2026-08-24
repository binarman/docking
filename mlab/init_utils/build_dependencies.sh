#!/bin/bash

trap 'echo "startup.sh received SIGINT or SIGTERM"; pkill -P $$' SIGINT SIGTERM

export GRADIO_ANALYTICS_ENABLED="False"
export DISABLE_TELEMETRY=1
export DO_NOT_TRACK=1
export HF_HUB_DISABLE_IMPLICIT_TOKEN=1
export HF_HUB_DISABLE_TELEMETRY=1

echo "initialize ComfyUI environment"
pip install torch torchvision torchaudio --extra-index-url https://download.pytorch.org/whl/cu130
pip install -r /tools/ComfyUI/requirements.txt

echo "build llama.cpp"
cd /tools/llama.cpp
LD_LIBRARY_PATH=/usr/local/cuda-13.0/compat/:$LD_LIBRARY_PATH
cmake -B build -DGGML_CUDA=ON
cmake --build build --config Release -j4
