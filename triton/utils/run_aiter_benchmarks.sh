#!/usr/bin/bash

if [ -z "$1" ] || ! (pwd|grep '/op_tests/op_benchmarks/triton$' > /dev/null) ;then
  echo "Usage:"
  echo "        Go to dir aiter/op_tests/op_benchmarks/triton"
  echo "        run_aiter_benchmarks.sh <workspace dir>"
  exit
fi

WORKSPACE="$1"

#set -xo pipefail
set -o pipefail

mkdir -p "$WORKSPACE"

exclude_list=("bench_topk.py" "bench_rope.py" 
              "bench_mla_decode_rope.py" "bench_mla_decode.py")

include_list=(bench_*.py)

for py_file in ${include_list[@]}; do
    [[ ! -f "$py_file" ]] && echo "No bench_*.py files found." && exit 1

    skip=false
    for exclude in ${exclude_list[@]}; do
        if [[ "$py_file" == "$exclude" ]]; then
            echo "Skipping: ${py_file}"
            skip=true
            break
        fi
    done

    if ${skip}; then
        continue
    fi

    log_name="$(basename ${py_file} .py).log"
#    echo "log name: ${log_name}"
    cmd="${CMD_PREFIX} python \"${py_file}\" &> \"${WORKSPACE}/${log_name}\""
    echo "Running: ${cmd}"
    eval "${cmd}"
done

