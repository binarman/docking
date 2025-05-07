#!/usr/bin/bash

git clone --depth 1 https://github.com/binarman/docking /tmp/docking

cp /tmp/docking/triton/config_files/bash_env "${HOME}/bash_env"
cp /tmp/docking/triton/config_files/vimrc "${HOME}/.vimrc"
cp /tmp/docking/triton/config_files/bash_aliases "${HOME}/.bash_aliases"

sudo cp -r /tmp/docking/triton/utils /utils

sudo apt -y update
sudo apt -y -f install
sudo apt -y install less strace wget psmisc gdb python3-pip python3-venv
git config --global user.name "Alexander Efimov"
git config --global user.email "efimov.alexander@gmail.com"
python3 -m venv "${HOME}/python_venv"
source "${HOME}/python_venv/bin/activate"
pip3 install pandas matplotlib pre-commit lit

# Initialize bashrc
mv "${HOME}/.bashrc" "${HOME}/bashrc.back"
echo "force_color_prompt=yes" | cat - "${HOME}/bashrc.back" > "${HOME}/.bashrc"
echo "source ~/bash_env" >> "${HOME}/.bashrc"

rm -rf /tmp/docking
