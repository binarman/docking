FROM ubuntu:24.04

RUN apt update; apt install -y openvpn
RUN mkdir -p /dev/net && mknod /dev/net/tun c 10 200 && chmod 600 /dev/net/tun
