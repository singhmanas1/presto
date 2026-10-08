#!/usr/bin/env python3
"""Run the parquet smoke query against a local Delta table.

The coordinator's delta catalog reads the table through the $path$ schema.
The native worker turns each Delta split into a HiveDeltaSplit. With cuDF
enabled, the scan uses the cuDF Delta reader and the sum stays on the GPU.
"""

import subprocess
import sys
from pathlib import Path

import run_smoke

# The Delta connector lowercases table names, including a quoted $path$ location.
# The directory itself has to be lowercase or the lookup misses it.
DELTA_DIR = Path("/home/nvidia/presto-delta-smoke/delta-numbers")


def write_delta_table(python):
    if DELTA_DIR.exists():
        run_smoke.shutil.rmtree(DELTA_DIR)
    DELTA_DIR.parent.mkdir(parents=True, exist_ok=True)
    subprocess.check_call(
        [str(python), "-m", "pip", "install", "deltalake"],
        stdout=subprocess.DEVNULL,
    )
    script = f"""
import pyarrow as pa
from deltalake import write_deltalake
n = {run_smoke.ROW_COUNT}
ids = pa.array(range(n), type=pa.int32())
values = pa.array([(i * 3) % 1000 for i in range(n)], type=pa.int64())
write_deltalake({str(DELTA_DIR)!r}, pa.table({{"id": ids, "value": values}}), mode="overwrite")
print("EXPECTED", sum((i * 3) % 1000 for i in range(n)), n)
"""
    out = subprocess.check_output([str(python), "-c", script], text=True)
    expected_line = next(line for line in out.splitlines() if line.startswith("EXPECTED "))
    _, expected_sum, expected_count = expected_line.split()
    expected_sum, expected_count = int(expected_sum), int(expected_count)
    run_smoke.log(f"wrote {DELTA_DIR} rows={expected_count} sum={expected_sum}")
    return expected_sum, expected_count


def main():
    for path in (run_smoke.LAUNCHER, run_smoke.CLI, run_smoke.WORKER_BIN):
        if not path.exists():
            raise SystemExit(f"missing {path}")
    env = run_smoke.java_env()
    python = run_smoke.ensure_venv()
    expected_sum, expected_count = write_delta_table(python)
    location = DELTA_DIR.resolve().as_uri()
    sql = f'SELECT sum(value), count(id) FROM delta."$path$"."{location}"'
    etc = run_smoke.write_coordinator_etc(delta=True)
    (run_smoke.SMOKE / "logs").mkdir(parents=True, exist_ok=True)
    try:
        presto_version = run_smoke.start_coordinator(etc, env)
        run_smoke.start_worker(False, presto_version, delta=True)
        run_smoke.wait_for_workers(1)
        cpu_output = run_smoke.query_sum(env, expected_sum, expected_count, sql)
        run_smoke.stop_worker()
        run_smoke.wait_for_workers(0)
        worker_log = run_smoke.start_worker(True, presto_version, delta=True)
        run_smoke.wait_for_workers(1)
        cudf_output = run_smoke.query_sum(env, expected_sum, expected_count, sql)
        gpu = run_smoke.gpu_snapshot()
        worker_text = worker_log.read_text(errors="replace")
        gpu_used = "presto_server" in gpu or str(run_smoke.worker_proc.pid) in gpu
        cudf_ran = "cuDF is registered." in worker_text and "CudfReduce" in worker_text
        run_smoke.log("----- delta smoke summary -----")
        run_smoke.log(f"sql={sql}")
        run_smoke.log(f"cpu_output={cpu_output}")
        run_smoke.log(f"cudf_output={cudf_output}")
        run_smoke.log(f"gpu_process_visible={int(gpu_used)}")
        run_smoke.log(f"cudf_operator_log={int(cudf_ran)}")
        run_smoke.log(gpu.rstrip())
        if not gpu_used:
            raise SystemExit("nvidia-smi did not show presto_server after the cuDF query")
        if not cudf_ran:
            raise SystemExit(f"cuDF worker log did not show GPU execution\n{run_smoke.tail(worker_log)}")
        run_smoke.log("delta_smoke_ok=1")
    finally:
        run_smoke.stop_worker()
        run_smoke.stop_coordinator()


if __name__ == "__main__":
    sys.exit(main())
