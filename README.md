Collection of docker files and utilities I use for development.

Contents:

- [/triton/amd](https://github.com/binarman/docking/tree/main/triton/amd): development infrastructure for OpenAI Triton on AMD hardware
- [/triton/nvidia](https://github.com/binarman/docking/tree/main/triton/nvidia): development infrastructure for OpenAI Triton on AMD hardware
- [/triton/config_files](https://github.com/binarman/docking/tree/main/triton/config_files): common configuration files tuning dev environment
- [/triton/utils](https://github.com/binarman/docking/tree/main/triton/utils): common scripts for all triton related containers

Each  directory contains files
- `make.sh` : script for composing docker image with development environment. Created image has name `alefimov-triton-img`
- `run.sh`  : script for creating or restarting existing container created on previous step. Default name of container is `alefimov-triton`, could be changed with `-n <custom name>` option

