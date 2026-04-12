#!/usr/bin/env bash

source user.config

docker run -it --rm --gpus all -v "${DEFAULT_MODELS_PATH}/ollama_models":/root/.ollama/models --name  "${DEFAULT_CONTAINER_NAME}" "${IMAGE_NAME}"
