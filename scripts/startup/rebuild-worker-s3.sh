#!/usr/bin/env bash
# Reconfigure the existing Prestissimo Release build with S3 enabled and
# relink presto_server. Leaves the Velox checkout where it is.
# Does not run make release, and does not rebuild the Java coordinator.
set -euo pipefail

ROOT=/home/nvidia/Presto
REPO="${ROOT}/presto/presto-native-execution"
LOG="${ROOT}/rebuild-worker-s3.log"

exec > >(tee -a "${LOG}") 2>&1

echo "=== $(date -Is) Prestissimo S3 rebuild ==="
echo "Log: ${LOG}"

export PATH="${HOME}/.local/bin:/usr/local/cuda/bin:${PATH}"
export CC=/usr/bin/gcc
export CXX=/usr/bin/g++
export USE_CLANG=false
export PROMPT_ALWAYS_RESPOND=n
export BUILD_FAISS=false
export CMAKE_POLICY_VERSION_MINIMUM="${CMAKE_POLICY_VERSION_MINIMUM:-3.5}"
export CUDA_ARCHITECTURES="${CUDA_ARCHITECTURES:-native}"
export CUDA_COMPILER="${CUDA_COMPILER:-/usr/local/cuda/bin/nvcc}"
export PRESTO_OPTIONAL_FEATURES=cudf,parquet,s3
export EXTRA_CMAKE_FLAGS="-DVELOX_ENABLE_S3=ON"
export MAX_LINK_JOBS="${MAX_LINK_JOBS:-4}"
export MAX_HIGH_MEM_JOBS="${MAX_HIGH_MEM_JOBS:-2}"
export BUILD_THREADS="${BUILD_THREADS:-8}"
export NUM_THREADS="${NUM_THREADS:-8}"
export SUDO="sudo --preserve-env env PATH=${PATH} CC=${CC} CXX=${CXX}"

if [[ ! -x "${CC}" || ! -x "${CXX}" ]]; then
  echo "GCC is required" >&2
  exit 1
fi
if [[ ! -x "${CUDA_COMPILER}" ]]; then
  echo "nvcc not found at ${CUDA_COMPILER}" >&2
  exit 1
fi
if [[ ! -f "${REPO}/CMakeLists.txt" ]]; then
  echo "missing ${REPO}" >&2
  exit 1
fi

cd "${REPO}"

SDK_BUILD="${REPO}/deps-download/aws-sdk-cpp/_build"
if [[ -f /usr/local/lib/cmake/AWSSDK/AWSSDKConfig.cmake || -f /usr/local/lib64/cmake/AWSSDK/AWSSDKConfig.cmake ]]; then
  echo "AWS SDK already installed"
elif [[ -f "${SDK_BUILD}/generated/src/aws-cpp-sdk-s3/libaws-cpp-sdk-s3.a" ]]; then
  echo "=== installing the already built AWS SDK ==="
  sudo --preserve-env env PATH="${PATH}" cmake --install "${SDK_BUILD}"
else
  echo "=== installing the AWS SDK (s3, identity-management) ==="
  ./velox/scripts/setup-ubuntu.sh install_aws_deps
fi

echo "=== reconfiguring with PRESTO_OPTIONAL_FEATURES=${PRESTO_OPTIONAL_FEATURES} ==="
make cmake-and-build

if ! grep -q 'PRESTO_ENABLE_S3:BOOL=ON' _build/release/CMakeCache.txt; then
  echo "PRESTO_ENABLE_S3 did not turn on" >&2
  exit 1
fi
if ! grep -q 'VELOX_ENABLE_S3:BOOL=ON' _build/release/CMakeCache.txt; then
  echo "VELOX_ENABLE_S3 did not turn on" >&2
  exit 1
fi
if ! grep -q 'PRESTO_ENABLE_CUDF:BOOL=ON' _build/release/CMakeCache.txt; then
  echo "PRESTO_ENABLE_CUDF is no longer on" >&2
  exit 1
fi

echo "=== $(date -Is) worker ready ==="
echo "Binary: ${REPO}/_build/release/presto_cpp/main/presto_server"
echo "Next: /home/nvidia/Presto/smoke/.venv/bin/python /home/nvidia/Presto/smoke/run_uc_s3.py"
