#!/usr/bin/env bash
# Rebuild the two coordinator jars that ask Unity Catalog for a temporary
# S3 key, and copy them into the already unpacked coordinator.
# Does not unpack a new server tarball, and does not rebuild the native worker.
set -euo pipefail

ROOT=/home/nvidia/Presto
REPO="${ROOT}/presto"
LOG="${ROOT}/update-coordinator-credentials.log"

exec > >(tee -a "${LOG}") 2>&1

echo "=== $(date -Is) update coordinator credential jars ==="

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
  echo "OpenJDK 17 was not found"
  exit 1
fi
export PATH="${JAVA_HOME}/bin:${PATH}"

cd "${REPO}"
./mvnw -pl presto-hive-metastore,presto-hive -am -DskipTests package

version=$(sed -n '/<artifactId>presto-root<\/artifactId>/{n;s/.*<version>\(.*\)<\/version>.*/\1/p;q}' pom.xml)
hive_jar="${REPO}/presto-hive/target/presto-hive-${version}.jar"
meta_jar="${REPO}/presto-hive-metastore/target/presto-hive-metastore-${version}.jar"
test -f "${hive_jar}"
test -f "${meta_jar}"

copy_over() {
  local source="$1"
  local name
  name="$(basename "${source}")"
  local found=0
  local dest
  while IFS= read -r dest; do
    cp -f "${source}" "${dest}"
    echo "updated ${dest}"
    found=1
  done < <(find "${ROOT}/coordinator/plugin" -name "${name}")
  if [[ "${found}" -eq 0 ]]; then
    echo "no installed copy of ${name} under ${ROOT}/coordinator/plugin"
    exit 1
  fi
}

copy_over "${hive_jar}"
copy_over "${meta_jar}"

echo "=== $(date -Is) credential jars installed ==="
echo "Next: /home/nvidia/Presto/smoke/.venv/bin/python /home/nvidia/Presto/smoke/run_uc_s3.py"
