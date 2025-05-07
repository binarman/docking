## Contents

- [/triton/config_files](https://github.com/binarman/docking/tree/main/triton/config_files): configuration and dockerfiles files for dev environment
- [/triton/utils](https://github.com/binarman/docking/tree/main/triton/utils): auxilary scripts

### Main scripts

These are scripts to create, run and manage containers

- `make.sh` : script for composing docker image with development environment. Created image has name `alefimov-triton-img`
- `run.sh`  : script for creating or restarting existing container created on previous step. Default name of container is `alefimov-triton`, could be changed with `-n <custom name>` option

### Auxilaty scrips:

These are scripts for managing triton builds and dev environment

- `fetch.sh`
- `lit_gen.py`
- `prepare_triton_repo.sh`
- `pull.sh`
- `push.sh`
- `run_tests.sh`
- `tweak_env.sh` : adjusts environment on non container host. works with arbitrary system user, but requires sudo.
- `update_scripts.sh`
