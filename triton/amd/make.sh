#!/usr/bin/bash

cp -r ../utils .
cp -r ../config_files .

docker build -t alefimov-triton-img .

rm -r utils
rm -r config_files
