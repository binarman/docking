#!/usr/bin/bash

set -e

cd $(git rev-parse --show-toplevel)
pytest --capture=tee-sys -rfs python/tutorials/06-fused-attention.py
pytest --capture=tee-sys -rfs third_party/amd/python/test/test_extract_slice.py

cd python/test/unit
pytest --capture=tee-sys -rfs -n 16 language runtime \
       --ignore=language/test_line_info.py \
       --ignore=test_debug.py
TRITON_DISABLE_LINE_INFO=0 python3 -m pytest -s -n 8 language/test_line_info.py
TRITON_INTERPRET=1 python3 -m pytest -s -n 16 -m interpreter language/test_core.py language/test_standard.py \
language/test_random.py language/test_block_pointer.py language/test_subprocess.py language/test_line_info.py \
runtime/test_autotuner.py::test_kwargs[False] ../../tutorials/06-fused-attention.py::test_op --device cpu
