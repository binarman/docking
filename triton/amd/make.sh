#!/usr/bin/bash

IMG_NAME="alefimov-triton-img"

cp -r ../utils .
cp -r ../config_files .

docker build -t "$IMG_NAME" .

rm -r utils
rm -r config_files
