#!/usr/bin/env python3
"""Query the local S3 Unity Catalog table through Presto.

The coordinator asks Unity Catalog for a temporary read key. The worker
opens s3://warehouse on 127.0.0.1:9000 with the local bucket key from its
catalog file. A pass prints "135","10".
"""

import sys

import run_smoke

SQL = "SELECT sum(value), count(id) FROM delta.smoke.cloud_numbers"
PROXY = "http://127.0.0.1:8082"


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
                "hive.metastore.unity.uri=" + PROXY,
                "hive.metastore.unity.catalog=clouds",
                "hive.s3.endpoint=http://127.0.0.1:9000",
                "hive.s3.path-style-access=true",
                "hive.s3.ssl.enabled=false",
                "",
            ]
        )
    )
    (run_smoke.SMOKE / "logs").mkdir(parents=True, exist_ok=True)
    failure_text = ""
    try:
        presto_version = run_smoke.start_coordinator(etc, env)
        run_smoke.start_worker(
            False,
            presto_version,
            delta=True,
            catalog_extra="\n".join(
                [
                    "s3.endpoint=http://127.0.0.1:9000",
                    "s3.endpoint.region=us-east-1",
                    "s3.aws-access-key=minioadmin",
                    "s3.aws-secret-key=minioadmin",
                    "s3.path-style-access=true",
                    "s3.ssl.enabled=false",
                    "s3.use-instance-credentials=false",
                    "s3.aws-imds-enabled=false",
                    "",
                ]
            ),
        )
        run_smoke.wait_for_workers(1)
        run_smoke.log(SQL)
        output = run_smoke.presto(env, SQL).strip()
        run_smoke.log(f"cpu_output={output}")
        if output.replace(" ", "") in {'"135","10"', "135,10"}:
            run_smoke.log("cloud_query_ok=1")
            return 0
        run_smoke.log("cloud_query_ok=0")
        return 1
    except SystemExit as failure:
        failure_text = str(failure)
        run_smoke.log(failure_text)
        run_smoke.log("----- coordinator log tail -----")
        run_smoke.log(run_smoke.tail(run_smoke.SMOKE / "logs" / "coordinator.log", 80))
        blob = failure_text + "\n" + run_smoke.tail(run_smoke.SMOKE / "logs" / "coordinator.log", 200)
        if (
            "No FileSystem for scheme: s3" in blob
            or "No registered file system matched" in blob
        ):
            run_smoke.log("coordinator_credentials_ok=1")
            run_smoke.log("worker_s3_missing=1")
            return 2
        return 1
    finally:
        run_smoke.stop_worker()
        run_smoke.stop_coordinator()


if __name__ == "__main__":
    sys.exit(main())
