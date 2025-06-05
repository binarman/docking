#!/usr/bin/bash

# if receive terminating signal, kill all childrens first
trap 'echo "startup script received SIGINT or SIGTERM"; pkill -P $$' SIGINT SIGTERM
# if one child dies(for example, x session is closed), kill all the others
trap 'echo "startup script received SIGCHLD"; pkill -P $$' SIGCHLD

fluxbox&
sleep infinity & wait
