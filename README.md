Collection of docker files and utilities I use for development.

## Contents

### Triton

- [/triton/amd](https://github.com/binarman/docking/tree/main/triton/amd): development infrastructure for OpenAI Triton on AMD hardware
- [/triton/nvidia](https://github.com/binarman/docking/tree/main/triton/nvidia): development infrastructure for OpenAI Triton on AMD hardware
- [/triton/config_files](https://github.com/binarman/docking/tree/main/triton/config_files): common configuration files tuning dev environment
- [/triton/utils](https://github.com/binarman/docking/tree/main/triton/utils): common scripts for all triton related containers

Each  directory contains files
- `make.sh` : script for composing docker image with development environment. Created image has name `alefimov-triton-img`
- `run.sh`  : script for creating or restarting existing container created on previous step. Default name of container is `alefimov-triton`, could be changed with `-n <custom name>` option

### Meeting alert

[/meeting_alert](https://github.com/binarman/docking/tree/main/meeting_alert): scripts to display status busy/free status in browser depending on is there meeting in progress. 

### Stable diffusion webui

[/sd_webui](https://github.com/binarman/docking/tree/main/sd_webui): infrastructure to deploy stable diffusion model on local machine.

### Utils

[/utils](https://github.com/binarman/docking/tree/main/utils): set of small non specific utils

[/utils/connect](https://github.com/binarman/docking/tree/main/utils/connect): connect to a given VPN

