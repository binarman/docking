#!/usr/bin/bash

source configs/defaults.config

docker run -it --rm --cap-add=NET_ADMIN "${DEFAULT_IMAGE_NAME}"
