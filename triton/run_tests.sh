#!/usr/bin/bash

set -e

pytest $(git rev-parse --show-toplevel)/python/test/unit/language/test_core.py -n16
pytest $(git rev-parse --show-toplevel)/python/test/unit/operators/test_flash_attention.py
