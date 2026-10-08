#!/usr/bin/env bash
# Build the Java Presto coordinator from the existing checkout.
# Does not run Maven clean, and does not touch the native worker build in
# presto-native-execution/_build.
set -euo pipefail

ROOT=/home/nvidia/Presto
REPO="${ROOT}/presto"
LOG="${ROOT}/build-coordinator.log"
DIST="${ROOT}/coordinator"

exec > >(tee -a "${LOG}") 2>&1

echo "=== $(date -Is) Presto coordinator build ==="
echo "Log: ${LOG}"

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
  echo "OpenJDK 17 is not installed. Installing it."
  sudo apt-get update
  sudo apt-get install -y openjdk-17-jdk
  find_java17
fi

export PATH="${JAVA_HOME}/bin:${PATH}"
echo "JAVA_HOME=${JAVA_HOME}"
java -version

cd "${REPO}"
# Maven 3.9 is downloaded by the wrapper on first use.
# -T 8 keeps the 96-core machine from launching a compiler per core.
# -DskipTests still compiles test jars. Modules such as presto-spi depend on
# presto-common's tests jar, and maven.test.skip omits that jar entirely.
# presto-docs needs extra tooling and is not part of the coordinator.
./mvnw -T 8 install \
  -DskipTests \
  -pl '!presto-docs'

version=$(sed -n '/<artifactId>presto-root<\/artifactId>/{n;s/.*<version>\(.*\)<\/version>.*/\1/p;q}' pom.xml)
tarball="presto-server/target/presto-server-${version}.tar.gz"
if [[ ! -f "${tarball}" ]]; then
  echo "Coordinator archive was not produced: ${tarball}"
  exit 1
fi

rm -rf "${DIST}"
mkdir -p "${DIST}"
tar -xzf "${tarball}" -C "${DIST}" --strip-components=1

echo "=== $(date -Is) coordinator build finished ==="
echo "Archive: ${REPO}/${tarball}"
echo "Launcher: ${DIST}/bin/launcher"
echo "CLI: ${REPO}/presto-cli/target/presto-cli-0.301-SNAPSHOT-executable.jar"
