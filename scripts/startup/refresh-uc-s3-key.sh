#!/usr/bin/env bash
# Ask the local S3 server for a new one-hour key, reload Unity Catalog, and
# rerun the cloud table query. Does not recreate the table.
set -euo pipefail

ROOT=/home/nvidia/Presto
VENV="${ROOT}/smoke/.venv"
UC_PORT=8082

if ! curl -sf "http://127.0.0.1:9000/minio/health/live" >/dev/null; then
  echo "local S3 server is not up on 127.0.0.1:9000" >&2
  exit 1
fi

"${VENV}/bin/python" "${ROOT}/uc/prepare_minio_credentials.py"
docker restart presto-uc >/dev/null

ready=0
for _ in $(seq 1 60); do
  if curl -sf "http://127.0.0.1:${UC_PORT}/api/2.1/unity-catalog/catalogs" >/dev/null; then
    ready=1
    break
  fi
  sleep 2
done
if [[ "${ready}" != 1 ]]; then
  echo "Unity Catalog did not become ready." >&2
  docker logs presto-uc || true
  exit 1
fi
echo "Unity Catalog reloaded with a new key."

"${VENV}/bin/python" "${ROOT}/smoke/run_uc_s3.py"
