#!/usr/bin/env bash
# Put one Unity Catalog managed Delta table on the local S3 server and query it.
# Spark writes clouds.smoke.cloud_numbers. This script drops and recreates that
# table. The coordinator then asks Unity Catalog for a temporary read key.
# The worker opens s3://warehouse with the local bucket password in its catalog.
# A pass prints "135","10". A later rerun that should keep the table uses
# refresh-uc-s3-key.sh, because the vended key lasts one hour.
set -euo pipefail

ROOT=/home/nvidia/Presto
LOG="${ROOT}/uc-s3-simulation.log"
UC_PORT=8082
VENV="${ROOT}/smoke/.venv"

exec > >(tee -a "${LOG}") 2>&1
echo "=== $(date -Is) Unity Catalog S3 credential simulation ==="

find_java17() {
  local candidate
  if [[ -n "${JAVA_HOME:-}" && -x "${JAVA_HOME}/bin/java" ]]; then
    if "${JAVA_HOME}/bin/java" -version 2>&1 | grep -q 'version "17\.'; then
      return 0
    fi
  fi
  shopt -s nullglob
  for candidate in /usr/lib/jvm/java-17-openjdk-* /usr/lib/jvm/java-17-*; do
    if [[ -x "${candidate}/bin/java" ]]; then
      export JAVA_HOME="${candidate}"
      return 0
    fi
  done
  shopt -u nullglob
  return 1
}

if ! find_java17; then
  echo "OpenJDK 17 was not found."
  exit 1
fi
export PATH="${JAVA_HOME}/bin:${PATH}"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is not installed."
  exit 1
fi

echo "=== starting MinIO ==="
MINIO_BIN="${ROOT}/uc/silo"
MINIO_DATA="${ROOT}/uc/minio-data"
mkdir -p "${MINIO_DATA}"
rm -f "${ROOT}/uc/minio"
if ! curl -sf "http://127.0.0.1:9000/minio/health/live" >/dev/null; then
  if [[ ! -x "${MINIO_BIN}" ]]; then
    echo "downloading the local S3 server"
    archive="$(mktemp)"
    curl -fL --retry 3 -o "${archive}" \
      "https://github.com/pgsty/silo/releases/download/RELEASE.2026-09-16T00-00-00Z/silo_20260916000000.0.0_linux_amd64.tar.gz"
    tar -xzf "${archive}" -C "${ROOT}/uc" silo
    chmod +x "${MINIO_BIN}"
    rm -f "${archive}"
  fi
  MINIO_ROOT_USER=minioadmin MINIO_ROOT_PASSWORD=minioadmin \
    "${MINIO_BIN}" server "${MINIO_DATA}" --address "127.0.0.1:9000" \
    > "${ROOT}/uc/minio.log" 2>&1 &
  echo $! > "${ROOT}/uc/minio.pid"
fi

ready=0
for _ in $(seq 1 60); do
  if curl -sf "http://127.0.0.1:9000/minio/health/live" >/dev/null; then
    ready=1
    break
  fi
  sleep 2
done
if [[ "${ready}" != 1 ]]; then
  echo "MinIO did not become ready."
  cat "${ROOT}/uc/minio.log" || true
  exit 1
fi
echo "MinIO is up."

if [[ ! -x "${VENV}/bin/python" ]]; then
  python3 -m venv "${VENV}"
fi
"${VENV}/bin/python" -m pip install 'boto3' 'pyspark==4.1.0'
"${VENV}/bin/python" "${ROOT}/uc/prepare_minio_credentials.py"

echo "=== reloading Unity Catalog with the S3 key ==="
if docker ps -a --format '{{.Names}}' | grep -qx presto-uc; then
  docker restart presto-uc >/dev/null
else
  mkdir -p /home/nvidia/presto-uc/warehouse
  docker run -d --name presto-uc \
    -p "${UC_PORT}:8080" \
    -v "/home/nvidia/presto-uc/warehouse:/home/nvidia/presto-uc/warehouse" \
    -v "${ROOT}/uc/server.properties:/home/unitycatalog/etc/conf/server.properties:ro" \
    unitycatalog/unitycatalog:v0.5.0
fi

ready=0
for _ in $(seq 1 60); do
  if curl -sf "http://127.0.0.1:${UC_PORT}/api/2.1/unity-catalog/catalogs" >/dev/null; then
    ready=1
    break
  fi
  sleep 2
done
if [[ "${ready}" != 1 ]]; then
  echo "Unity Catalog did not become ready."
  docker logs presto-uc || true
  exit 1
fi
echo "Unity Catalog is up."

echo "=== creating the managed table on s3://warehouse ==="
UC_URL="http://127.0.0.1:${UC_PORT}" \
  UC_CATALOG=clouds \
  MINIO_ENDPOINT="http://127.0.0.1:9000" \
  "${VENV}/bin/python" "${ROOT}/uc/create_s3_managed_table.py"

echo "=== queries from Presto ==="
"${VENV}/bin/python" "${ROOT}/smoke/run_uc_s3.py"
echo "=== $(date -Is) S3 simulation finished ==="
