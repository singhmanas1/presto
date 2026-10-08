#!/usr/bin/env bash
# Give Unity Catalog a fake key, rerun the cloud query, then put the real key back.
# The worker still uses the local bucket password from its own config.
set -uo pipefail
ROOT=/home/nvidia/Presto
PROPS="${ROOT}/uc/server.properties"
BAK="$(mktemp)"
cp "${PROPS}" "${BAK}"
restore() {
  cp "${BAK}" "${PROPS}"
  rm -f "${BAK}"
  docker restart presto-uc >/dev/null
  echo "real key restored"
}
trap restore EXIT
python3 - << 'PY'
from pathlib import Path
p = Path("/home/nvidia/Presto/uc/server.properties")
out = []
for line in p.read_text().splitlines():
    if line.startswith("s3.accessKey.0="):
        out.append("s3.accessKey.0=BOGUSCOORDINATORKEY")
    elif line.startswith("s3.secretKey.0="):
        out.append("s3.secretKey.0=bogus-secret")
    elif line.startswith("s3.sessionToken.0="):
        out.append("s3.sessionToken.0=bogus-session-token")
    else:
        out.append(line)
p.write_text("\n".join(out) + "\n")
print("vended key is now fake")
PY
docker restart presto-uc >/dev/null
ready=0
for _ in $(seq 1 60); do
  if curl -sf "http://127.0.0.1:8082/api/2.1/unity-catalog/catalogs" >/dev/null; then
    ready=1
    break
  fi
  sleep 2
done
if [[ "${ready}" != 1 ]]; then
  echo "Unity Catalog did not become ready." >&2
  exit 1
fi
echo "Unity Catalog is handing out the fake key"
"${ROOT}/smoke/.venv/bin/python" "${ROOT}/smoke/run_uc_s3.py"
echo "query_exit=$?"
