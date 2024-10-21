#!/usr/bin/bash
while true; do
  running_streams=$(grep -R RUNNING /proc/asound/|wc -l)
  if [ $running_streams -gt 3 ]; then
    wget http://aefimov-pc:8080/busy -O /dev/null
  else
    wget http://aefimov-pc:8080/free -O /dev/null
  fi
  sleep 5;
done
