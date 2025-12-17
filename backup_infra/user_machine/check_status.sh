#!/bin/bash

if [ "$#" -ne 1 ]; then
  echo "Required server address as a parameter"
  exit 1
fi

SERVER="$1"

server_failed=0
msg="$(date '+%c')\n\n"

/bin/ping -c 1 "$SERVER"
if [ $? -ne 0 ]
then
 server_failed=1
 msg="${msg}- ping failed\n"
fi

/usr/bin/ssh "$SERVER" true
if [ $? -ne 0 ]
then
  server_failed=1
  msg="${msg}- ssh session failed\n"
fi

#temperatures=$(/usr/bin/ssh "$SERVER" sensors|/bin/sed -n 's/[^+]*+\([0-9]*\.[0-9]\)°C.*/\1/p')
#if [[ ${temperatures} == "" ]]
#then
#  server_failed=1
#  msg="${msg}- failed to gather server temperature information\n"
#fi
#for temp in ${temperatures}; do
#  if [[ $(echo "$temp > 50"|/usr/bin/bc) -eq 1 ]]; then
#    server_failed=1
#    msg="${msg}- some component temperature is ${temp}°C\n"
#  fi
#done
temperature=$(/usr/bin/ssh "$SERVER" echo $[ $(cat /sys/devices/virtual/thermal/thermal_zone*/temp | sort -n|tail -n 1) / 1000 ])
if [[ ${temperature} == "" ]]
then
  server_failed=1
  msg="${msg}- failed to gather server temperature information\n"
fi
if (( $temperature > 60 )); then
  server_failed=1
  msg="${msg}- some component temperature is ${temperature}°C\n"
fi

IFS=$'\n'
disks_status=$(/usr/bin/ssh "$SERVER" /home/binarman/check_disk_smart.sh)
if [ $? -ne 0 ]; then
  server_failed=1
  for line in ${disks_status}; do
    msg="${msg}- ${line}\n"
  done
  if [[ ${disk_status} = "" ]]
  then
    msg="${msg}- can not check server SMART attributes\n"
  fi
fi

/usr/bin/ssh "$SERVER" mount | /bin/grep /storage
if [ $? -ne 0 ]
then
  server_failed=1
  msg="${msg}- storage disk is not functional on server\n"
fi

#/bin/mount | /bin/grep shared
#if [ $? -ne 0 ]
#then
#  server_failed=1
#  msg="${msg}- shared disk is not mounted\n"
#fi

#/usr/bin/ssh "$SERVER" cat /proc/mdstat | /bin/grep '\[[_U]*\]' | grep _
#if [ $? -eq 0 ]
#then
#  server_failed=1
#  msg="${msg}- RAID degraided\n"
#fi

ERROR_LOG_FILE="/tmp/error_guard.log"
if [ -e ${ERROR_LOG_FILE} ]
then
  server_failed=1
  msg="${msg}- local error guard log is not empty:\n"
  IFS=$'\n'
  for line in $(/usr/bin/tail -n 10 ${ERROR_LOG_FILE}); do
    msg="${msg}    - ${line}\n"
  done
fi
if /usr/bin/ssh "$SERVER" test -e ${ERROR_LOG_FILE}
then
  server_failed=1
  msg="${msg}- server error guard log is not empty:\n"
  IFS=$'\n'
  for line in $(/usr/bin/ssh "$SERVER" tail -n 10 ${ERROR_LOG_FILE}); do
    msg="${msg}    - ${line}\n"
  done
fi

echo "server is failed: ${server_failed}"
export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus
export DISPLAY=:0.0
if [ $server_failed -eq 1 ]
then
  /usr/bin/notify-send -t 43200000 -i /usr/custom/scripts/error.png "SERVER FAILED" " ${msg}"
else
  /usr/bin/notify-send -t 43200000 -i /usr/custom/scripts/ok.png "SERVER OK"
fi

