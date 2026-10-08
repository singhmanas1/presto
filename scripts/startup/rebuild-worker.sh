#!/bin/bash
# Relink presto_server after the Delta connector change. The existing Release
# cache is reused, so this does not reconfigure Velox or rebuild cuDF.
set -euo pipefail
cd /home/nvidia/Presto/presto/presto-native-execution
cmake --build _build/release --target presto_server -j 8
echo "worker ready: _build/release/presto_cpp/main/presto_server"
