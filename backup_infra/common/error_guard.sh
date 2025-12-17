#!/bin/bash

ERROR_LOG_FILE=/tmp/error_guard.log

if [ $# -eq 0 ]
then
  /bin/date >> ${ERROR_LOG_FILE}
  echo "expecting at least one argument" | /usr/bin/tee -a ${ERROR_LOG_FILE}
  echo >> ${ERROR_LOG_FILE}
  exit 1
fi

result=$("$@" 2>&1)

if [ $? -ne 0 ]
then
  /bin/date >> ${ERROR_LOG_FILE}
  echo "$@" >> ${ERROR_LOG_FILE}
  echo "${result}" >> ${ERROR_LOG_FILE}
  echo >> ${ERROR_LOG_FILE}
fi

