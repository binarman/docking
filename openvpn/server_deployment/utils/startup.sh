#!/usr/bin/bash

mkdir -p /dev/net && mknod /dev/net/tun c 10 200 && chmod 600 /dev/net/tun
dnsmasq

iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
iptables -A FORWARD -i tun0 -o eth0 -j ACCEPT
iptables -A FORWARD -i eth0 -o tun0 -m state --state ESTABLISHED,RELATED -j ACCEPT

cd /etc/openvpn/server/
/sbin/openvpn --status /run/openvpn-server/status-server.log --status-version 2 --suppress-timestamps --config server.conf
