#!/usr/bin/bash

source configs/defaults.config

docker build -t "${DEFAULT_IMAGE_NAME}" .
