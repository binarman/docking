#!/usr/bin/bash
cd /
/usr/sbin/sshd -D&
gunicorn -b0.0.0.0:8080 server:app
