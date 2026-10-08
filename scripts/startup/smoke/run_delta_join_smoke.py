#!/usr/bin/env python3
"""Inner-join two local Delta tables through the coordinator.

The left table has one row per id. The right table keeps every 10th id.
The join sum is smaller than the left table alone, so a scan that skips
the join cannot pass. cuDF must replace the hash join, not only the sum.
"""

import subprocess
import sys
from pathlib import Path

import run_smoke

# The Delta connector lowercases a quoted $path$ location.
LEFT_DIR = Path("/home/nvidia/presto-delta-smoke/join-left")
RIGHT_DIR = Path("/home/nvidia/presto-delta-smoke/join-right")


def write_tables(python):
    for path in (LEFT_DIR, RIGHT_DIR):
        if path.exists():
            run_smoke.shutil.rmtree(path)
    LEFT_DIR.parent.mkdir(parents=True, exist_ok=True)
    subprocess.check_call(
        [str(python), "-m", "pip", "install", "deltalake"],
        stdout=subprocess.DEVNULL,
    )
    script = f"""
import pyarrow as pa
from deltalake import write_deltalake
n = {run_smoke.ROW_COUNT}
ids = list(range(n))
values = [(i * 3) % 1000 for i in ids]
right_ids = [i for i in ids if i % 10 == 0]
write_deltalake({str(LEFT_DIR)!r}, pa.table({{
    "id": pa.array(ids, type=pa.int32()),
    "value": pa.array(values, type=pa.int64()),
}}), mode="overwrite")
write_deltalake({str(RIGHT_DIR)!r}, pa.table({{
    "id": pa.array(right_ids, type=pa.int32()),
}}), mode="overwrite")
print("EXPECTED", sum(values[i] for i in right_ids), len(right_ids))
"""
    out = subprocess.check_output([str(python), "-c", script], text=True)
    expected_line = next(line for line in out.splitlines() if line.startswith("EXPECTED "))
    _, expected_sum, expected_count = expected_line.split()
    expected_sum, expected_count = int(expected_sum), int(expected_count)
    run_smoke.log(
        f"wrote {LEFT_DIR} rows={run_smoke.ROW_COUNT} and {RIGHT_DIR} rows={expected_count} "
        f"join_sum={expected_sum}"
    )
    return expected_sum, expected_count


def main():
    for path in (run_smoke.LAUNCHER, run_smoke.CLI, run_smoke.WORKER_BIN):
        if not path.exists():
            raise SystemExit(f"missing {path}")
    env = run_smoke.java_env()
    python = run_smoke.ensure_venv()
    expected_sum, expected_count = write_tables(python)
    left = LEFT_DIR.resolve().as_uri()
    right = RIGHT_DIR.resolve().as_uri()
    sql = (
        "SELECT sum(l.value), count(l.id) "
        f'FROM delta."$path$"."{left}" l '
        f'INNER JOIN delta."$path$"."{right}" r ON l.id = r.id'
    )
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
        cudf_ran = "cuDF is registered." in worker_text and "CudfHashJoinProbe" in worker_text
        run_smoke.log("----- delta join summary -----")
        run_smoke.log(f"sql={sql}")
        run_smoke.log(f"cpu_output={cpu_output}")
        run_smoke.log(f"cudf_output={cudf_output}")
        run_smoke.log(f"gpu_process_visible={int(gpu_used)}")
        run_smoke.log(f"cudf_join_log={int(cudf_ran)}")
        run_smoke.log(gpu.rstrip())
        if not gpu_used:
            raise SystemExit("nvidia-smi did not show presto_server after the cuDF query")
        if not cudf_ran:
            raise SystemExit(f"cuDF worker log did not show a GPU hash join\n{run_smoke.tail(worker_log)}")
        run_smoke.log("delta_join_ok=1")
    finally:
        run_smoke.stop_worker()
        run_smoke.stop_coordinator()


if __name__ == "__main__":
    sys.exit(main())
