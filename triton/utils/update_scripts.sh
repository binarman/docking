#!/usr/bin/bash

git clone --depth 1 https://github.com/binarman/docking /utils/docking
cp /utils/docking/triton_amd/utils/* /utils/
rm -rf /utils/docking
