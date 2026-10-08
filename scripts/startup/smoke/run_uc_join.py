#!/usr/bin/env python3
"""Run the Delta join through a Unity Catalog lookup.

The coordinator asks Unity Catalog for storage_location and then opens that
directory with the existing Delta reader. This script does not change the reader.
"""

import run_smoke

SQL = (
    "SELECT sum(l.value), count(l.id) "
    "FROM delta.smoke.join_left l "
    "INNER JOIN delta.smoke.join_right r ON l.id = r.id"
)


def main():
    for path in (run_smoke.LAUNCHER, run_smoke.CLI, run_smoke.WORKER_BIN):
        if not path.exists():
            raise SystemExit(f"missing {path}")
    env = run_smoke.java_env()
    etc = run_smoke.write_coordinator_etc(delta=True)
    (etc / "catalog" / "delta.properties").write_text(
        "\n".join(
            [
                "connector.name=delta",
                "hive.metastore=unity",
                "hive.metastore.unity.uri=http://127.0.0.1:8082",
                "hive.metastore.unity.catalog=unity",
                "",
            ]
        )
    )
    (run_smoke.SMOKE / "logs").mkdir(parents=True, exist_ok=True)
    try:
        presto_version = run_smoke.start_coordinator(etc, env)
        run_smoke.start_worker(False, presto_version, delta=True)
        run_smoke.wait_for_workers(1)
        run_smoke.log(SQL)
        output = run_smoke.presto(env, SQL).strip()
        run_smoke.log(f"cpu_output={output}")
        run_smoke.stop_worker()
        run_smoke.wait_for_workers(0)
        worker_log = run_smoke.start_worker(True, presto_version, delta=True)
        run_smoke.wait_for_workers(1)
        output = run_smoke.presto(env, SQL).strip()
        run_smoke.log(f"cudf_output={output}")
        run_smoke.log(run_smoke.gpu_snapshot().rstrip())
        run_smoke.log(f"cudf_join_log={int('CudfHashJoinProbe' in worker_log.read_text(errors='replace'))}")
    except SystemExit as failure:
        run_smoke.log(str(failure))
        run_smoke.log("----- coordinator log tail -----")
        run_smoke.log(run_smoke.tail(run_smoke.SMOKE / "logs" / "coordinator.log", 120))
        raise
    finally:
        run_smoke.stop_worker()
        run_smoke.stop_coordinator()


if __name__ == "__main__":
    main()
