Collection of docker files and utilities I use for development.

## Contents

### Triton

- [/triton/](https://github.com/binarman/docking/tree/main/triton/amd): development infrastructure for OpenAI Triton on AMD and Nvidia hardware
- [/triton/config_files](https://github.com/binarman/docking/tree/main/triton/config_files): configuration and dockerfiles files for dev environment
- [/triton/utils](https://github.com/binarman/docking/tree/main/triton/utils): common scripts for triton related containers

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

### OpenVPN

[/openvpn](https://github.com/binarman/docking/tree/main/openvpn): container and scripts for simple open vpn deployment

### Dockerized desktop

[/dockerized_gui](https://github.com/binarman/docking/tree/main/dockerized_gui): infrastructure for minimal desktop running inside a container
