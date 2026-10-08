#!/usr/bin/env bash
# Build Prestissimo (Presto C++) with cuDF, using
# https://github.com/JohnZed/velox branch delta-lake-cudf as the Velox submodule.
#
# Ubuntu 24.04, CUDA 13.3 at /usr/local/cuda. Setup installs apt packages and
# writes libraries to /usr/local, so this script needs sudo.
set -euo pipefail

ROOT=/home/nvidia/Presto
REPO="${ROOT}/presto"
LOG="${ROOT}/build-prestissimo-cudf.log"
VELOX_URL=https://github.com/JohnZed/velox.git
VELOX_BRANCH=delta-lake-cudf

export USE_CLANG=true
# FAISS is optional vector search. Velox and Prestissimo leave it off.
# Ubuntu 24.04's libomp-dev is LLVM 18, which conflicts with the Clang 15
# OpenMP package, so the FAISS dependency build cannot be configured.
export BUILD_FAISS=false
export PROMPT_ALWAYS_RESPOND=n
export CMAKE_POLICY_VERSION_MINIMUM="${CMAKE_POLICY_VERSION_MINIMUM:-3.5}"
export CUDA_ARCHITECTURES="${CUDA_ARCHITECTURES:-native}"
export CUDA_COMPILER="${CUDA_COMPILER:-/usr/local/cuda/bin/nvcc}"
export PRESTO_OPTIONAL_FEATURES="${PRESTO_OPTIONAL_FEATURES:-cudf,parquet}"
export MAX_LINK_JOBS="${MAX_LINK_JOBS:-4}"
export MAX_HIGH_MEM_JOBS="${MAX_HIGH_MEM_JOBS:-2}"
# uv installs CMake into ~/.local/bin. sudo's secure_path does not search there,
# so "sudo cmake" fails. Run the real command through env so PATH is applied
# after sudo replaces it.
export PATH="${HOME}/.local/bin:/usr/local/cuda/bin:${PATH}"
export SUDO="sudo --preserve-env env PATH=${PATH}"
# setup-ubuntu.sh is sourced by Prestissimo, which skips the block that exports
# these. Without them, dependencies compile with /usr/bin/c++.
export CC=/usr/bin/clang-15
export CXX=/usr/bin/clang++-15

exec > >(tee -a "${LOG}") 2>&1

echo "=== $(date -Is) Prestissimo cuDF build ==="
echo "Log: ${LOG}"

if [[ ! -x "${CUDA_COMPILER}" ]]; then
  echo "nvcc not found at ${CUDA_COMPILER}" >&2
  exit 1
fi
echo "CUDA compiler: $("${CUDA_COMPILER}" --version | tail -n 1)"

sudo -v

if [[ ! -d "${REPO}/.git" ]]; then
  git clone https://github.com/prestodb/presto.git "${REPO}"
fi

cd "${REPO}"
git submodule set-url presto-native-execution/velox "${VELOX_URL}"
git submodule set-branch --branch "${VELOX_BRANCH}" presto-native-execution/velox
git submodule sync -- presto-native-execution/velox

# Clone the fork if the upstream gitlink is not in this branch.
if [[ ! -d presto-native-execution/velox/.git ]]; then
  if ! git submodule update --init presto-native-execution/velox; then
    rm -rf presto-native-execution/velox
    git clone --branch "${VELOX_BRANCH}" "${VELOX_URL}" presto-native-execution/velox
  fi
fi

git -C presto-native-execution/velox fetch origin "${VELOX_BRANCH}"
# Do not reset when already on this tip. Local edits to the setup scripts must survive a rerun.
if [[ "$(git -C presto-native-execution/velox rev-parse HEAD)" != "$(git -C presto-native-execution/velox rev-parse "origin/${VELOX_BRANCH}")" ]]; then
  git -C presto-native-execution/velox checkout -B "${VELOX_BRANCH}" "origin/${VELOX_BRANCH}"
fi
# make release would otherwise reset Velox to the SHA pinned by prestodb/presto.
git config "submodule.presto-native-execution/velox.update" none

echo "Velox HEAD: $(git -C presto-native-execution/velox rev-parse --short HEAD) ($(git -C presto-native-execution/velox log -1 --format=%s))"

cd "${REPO}/presto-native-execution"

# The setup script treats an existing _build as finished when
# PROMPT_ALWAYS_RESPOND=n. Drop incomplete builds so they are installed again.
rm -rf deps-download/fmt/_build deps-download/s2geometry/_build deps-download/faiss/_build deps-download/proxygen/_build

if [[ ! -x "${CC}" || ! -x "${CXX}" ]]; then
  echo "clang-15 is not installed yet; setup-ubuntu.sh will install it" >&2
fi
if ! command -v cmake >/dev/null 2>&1; then
  echo "cmake is not on PATH (${PATH})" >&2
  exit 1
fi
echo "Using $(command -v cmake) ($("${CC:-cc}" --version | head -n 1))"

./scripts/setup-ubuntu.sh

hash -r

# cuDF on this Velox branch refuses anything but GCC >= 13.3. Dependencies
# were built with Clang 15 against libstdc++; the Prestissimo compile uses
# the system GCC, which nvcc also uses as its host compiler.
export CC=/usr/bin/gcc
export CXX=/usr/bin/g++
if [[ ! -x "${CC}" || ! -x "${CXX}" ]]; then
  echo "GCC is required for the cuDF build" >&2
  exit 1
fi
# Drop a configure cache that still points at Clang. An existing GCC build
# is reused.
if [[ -f _build/release/CMakeCache.txt ]] && grep -q 'CMAKE_CXX_COMPILER:.*clang' _build/release/CMakeCache.txt; then
  rm -rf _build/release
fi

echo "C compiler: $("${CC}" --version | head -n 1)"
echo "CMake: $(cmake --version | head -n 1)"
cmake_major="$(cmake --version | awk 'NR==1 {print $3}' | cut -d. -f1)"
if [[ "${cmake_major}" -lt 4 ]]; then
  echo "CMake >= 4 is required by the cuDF pin on this branch. Found: $(cmake --version | head -n 1)" >&2
  exit 1
fi

# cmake-and-build does not run `git submodule update`, so the Velox checkout stays put.
make cmake-and-build

echo "=== $(date -Is) build finished ==="
echo "Worker binary:"
find _build/release -name presto_server -type f -print
