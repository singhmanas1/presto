#!/bin/bash
# Point the Presto tree at John Zedlewski's delta-lake-cudf branch.
# Velox is already the commit that branch pins (a0d129eaa), so this does not
# reconfigure or rebuild it. The native _build directory and downloaded deps
# stay in place.
set -euo pipefail
cd /home/nvidia/Presto/presto

git remote add johnzed https://github.com/JohnZed/presto.git 2>/dev/null || true
git fetch johnzed delta-lake-cudf

# Local Delta translator and other Presto edits are kept in a stash, then
# the worktree is replaced with his branch. Ignored build outputs are left alone.
if ! git diff --quiet || ! git diff --cached --quiet || [ -n "$(git ls-files --others --exclude-standard)" ]; then
  git stash push -u -m "local presto edits before johnzed delta-lake-cudf"
fi

git checkout -B delta-lake-cudf johnzed/delta-lake-cudf

# Drop setup-script edits inside Velox. Do not fetch the submodule: his
# .gitmodules URL is facebookincubator/velox, and this commit lives on
# JohnZed/velox, which is already checked out.
git -C presto-native-execution/velox checkout -- .
git -C presto-native-execution/velox clean -fd

# libproxygen.a on this machine needs libcares listed after it.
cmake_lists=presto-native-execution/CMakeLists.txt
if ! grep -q 'find_library(CARES' "$cmake_lists"; then
  python3 - "$cmake_lists" <<'PY'
import pathlib, sys
path = pathlib.Path(sys.argv[1])
text = path.read_text()
text = text.replace(
    "find_library(PROXYGEN_HTTPSERVER proxygen_httpserver)\n",
    "find_library(PROXYGEN_HTTPSERVER proxygen_httpserver)\nfind_library(CARES NAMES cares REQUIRED)\n",
    1,
)
old = "  ${MVFST}\n)"
new = "  ${MVFST}\n  ${CARES}\n)"
if old not in text:
    raise SystemExit("could not find PROXYGEN_LIBRARIES mvfst entry")
path.write_text(text.replace(old, new, 1))
PY
  echo "reapplied local cares link line"
fi

echo "presto $(git rev-parse --abbrev-ref HEAD) $(git rev-parse --short HEAD)"
echo "velox  $(git -C presto-native-execution/velox rev-parse --short HEAD)"
