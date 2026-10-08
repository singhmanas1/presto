#!/usr/bin/env bash
# Stand up a local Unity Catalog, create two managed Delta tables, and run
# the join through the coordinator. Requires Delta Kernel 4.4.0 or higher.
set -euo pipefail

ROOT=/home/nvidia/Presto
REPO="${ROOT}/presto"
LOG="${ROOT}/uc-simulation.log"
UC_PORT=8082
WAREHOUSE=/home/nvidia/presto-uc/warehouse

exec > >(tee -a "${LOG}") 2>&1
echo "=== $(date -Is) Unity Catalog managed Delta simulation ==="

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
echo "JAVA_HOME=${JAVA_HOME}"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is not installed. The Unity Catalog server was not started."
  exit 1
fi

mkdir -p "${WAREHOUSE}"
echo "=== starting Unity Catalog on port ${UC_PORT} ==="
docker rm -f presto-uc >/dev/null 2>&1 || true
docker run -d --name presto-uc \
  -p "${UC_PORT}:8080" \
  -v "${WAREHOUSE}:${WAREHOUSE}" \
  -v "${ROOT}/uc/server.properties:/home/unitycatalog/etc/conf/server.properties:ro" \
  unitycatalog/unitycatalog:v0.5.0

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
curl -sS "http://127.0.0.1:${UC_PORT}/api/2.1/unity-catalog/catalogs"
echo

# Spark's resolver finds this module from the local Maven cache because the
# pom is present, then refuses to download the jar from Maven Central.
jackson_jar="${HOME}/.m2/repository/org/apache/parquet/parquet-jackson/1.16.0/parquet-jackson-1.16.0.jar"
if [[ ! -s "${jackson_jar}" ]]; then
  echo "=== fetching missing parquet-jackson jar ==="
  mkdir -p "$(dirname "${jackson_jar}")"
  curl -fL --retry 3 -o "${jackson_jar}" \
    "https://repo1.maven.org/maven2/org/apache/parquet/parquet-jackson/1.16.0/parquet-jackson-1.16.0.jar"
fi

echo "=== creating managed Delta tables with Spark ==="
VENV="${ROOT}/smoke/.venv"
if [[ ! -x "${VENV}/bin/python" ]]; then
  python3 -m venv "${VENV}"
fi
"${VENV}/bin/python" -m pip install 'pyspark==4.1.0'
UC_URL="http://127.0.0.1:${UC_PORT}" UC_CATALOG=unity "${VENV}/bin/python" "${ROOT}/uc/create_managed_tables.py"

echo "=== Unity Catalog table records ==="
for table in join_left join_right; do
  echo "----- GET unity.smoke.${table} -----"
  curl -sS "http://127.0.0.1:${UC_PORT}/api/2.1/unity-catalog/tables/unity.smoke.${table}"
  echo
done

echo "=== rebuilding the coordinator with the unity metastore ==="
cd "${REPO}"
./mvnw -T 8 install -DskipTests -pl '!presto-docs'
version=$(sed -n '/<artifactId>presto-root<\/artifactId>/{n;s/.*<version>\(.*\)<\/version>.*/\1/p;q}' pom.xml)
tarball="presto-server/target/presto-server-${version}.tar.gz"
if [[ ! -f "${tarball}" ]]; then
  echo "Coordinator archive was not produced: ${tarball}"
  exit 1
fi
rm -rf "${ROOT}/coordinator"
mkdir -p "${ROOT}/coordinator"
tar -xzf "${tarball}" -C "${ROOT}/coordinator" --strip-components=1

echo "=== running the join ==="
"${VENV}/bin/python" "${ROOT}/smoke/run_uc_join.py"
echo "=== $(date -Is) simulation finished ==="
