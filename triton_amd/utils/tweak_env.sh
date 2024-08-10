#!/usr/bin/bash

git clone --depth 1 https://github.com/binarman/docking /docking

cp /docking/triton_amd/bashrc_addon /root/bashrc_addon
cp /docking/triton_amd/vimrc /root/.vimrc
cp /docking/triton_amd/bash_aliases /root/.bash_aliases

cp -r /docking/triton_amd/utils /utils

apt -y update
apt -y -f install
apt -y install less strace wget psmisc
git config --global user.name "Alexander Efimov"
git config --global user.email "efimov.alexander@gmail.com"
pip3 install pandas matplotlib pre-commit lit

# Initialize bashrc
mv /root/.bashrc /root/bashrc.back
echo "force_color_prompt=yes" | cat - /root/bashrc.back /root/bashrc_addon > /root/.bashrc

rm -rf /docking
