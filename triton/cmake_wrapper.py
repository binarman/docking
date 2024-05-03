#!/usr/bin/env python3

import os
import subprocess
import sys 
import json

cmake_arg_dump = "CMAKE_ARG_DUMP"
if cmake_arg_dump in os.environ:
    dump_file = os.environ[cmake_arg_dump]
    with open(dump_file, "w") as f:
        settings = {"cmake.configureArgs": sys.argv[1:]}
        json.dump(settings, f, indent=4)

subprocess.call(["/opt/conda/envs/py_3.9/bin/cmake"] + sys.argv[1:])

