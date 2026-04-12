#!/usr/bin/env bash
source user.config
docker build -t "$IMAGE_NAME" .
