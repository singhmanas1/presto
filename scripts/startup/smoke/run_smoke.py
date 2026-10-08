#!/usr/bin/env python3
"""SQL smoke test through the Java coordinator and the native worker.

The coordinator parses the SQL. The native worker only receives the planned
task. One run leaves cuDF off. The next starts the same worker binary with
cudf.enabled=true.
"""

import csv
import glob
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path("/home/nvidia/Presto")
SMOKE = ROOT / "smoke"
VENV = SMOKE / ".venv"
DATA = SMOKE / "data" / "smoke.parquet"
NATIVE = ROOT / "presto" / "presto-native-execution"
BUILD = NATIVE / "_build" / "release"
WORKER_BIN = BUILD / "presto_cpp" / "main" / "presto_server"
COORDINATOR = ROOT / "coordinator"
LAUNCHER = COORDINATOR / "bin" / "launcher"
def find_cli():
    matches = list((ROOT / "presto" / "presto-cli" / "target").glob("presto-cli-*-executable.jar"))
    if not matches:
        raise SystemExit("missing presto-cli executable jar")
    return max(matches, key=lambda path: path.stat().st_mtime)


CLI = find_cli()
ROW_COUNT = 100_000
COORDINATOR_PORT = 8080
WORKER_PORT = 8081

coordinator_proc = None
worker_proc = None


def log(message):
    print(message, flush=True)


def java_env():
    home = os.environ.get("JAVA_HOME", "")
    if home and Path(home, "bin/java").exists():
        version = subprocess.check_output(
            [str(Path(home, "bin/java")), "-version"], stderr=subprocess.STDOUT, text=True
        )
        if 'version "17.' in version:
            env = os.environ.copy()
            env["JAVA_HOME"] = home
            env["PATH"] = f"{home}/bin:{env.get('PATH', '')}"
            return env
    for candidate in sorted(glob.glob("/usr/lib/jvm/java-17-openjdk-*")):
        if Path(candidate, "bin/java").exists():
            env = os.environ.copy()
            env["JAVA_HOME"] = candidate
            env["PATH"] = f"{candidate}/bin:{env.get('PATH', '')}"
            return env
    raise SystemExit("OpenJDK 17 was not found. Run build-coordinator.sh first.")


def library_path():
    paths = []
    for candidate in [
        Path("/usr/local/cuda/lib64"),
        Path("/usr/local/lib"),
        BUILD / "_deps" / "cudf-build",
        BUILD / "_deps" / "rmm-build",
        BUILD / "_deps" / "kvikio-build",
    ]:
        if candidate.is_dir():
            paths.append(str(candidate))
    extra = os.environ.get("LD_LIBRARY_PATH", "")
    if extra:
        paths.append(extra)
    return ":".join(paths)


def ensure_venv():
    python = VENV / "bin" / "python"
    if not python.exists():
        log(f"creating virtualenv {VENV}")
        subprocess.check_call([sys.executable, "-m", "venv", str(VENV)])
    subprocess.check_call(
        [str(python), "-m", "pip", "install", "--disable-pip-version-check", "pyarrow"]
    )
    return python


def write_parquet(python):
    DATA.parent.mkdir(parents=True, exist_ok=True)
    script = f"""
import pyarrow as pa
import pyarrow.parquet as pq
n = {ROW_COUNT}
ids = pa.array(range(n), type=pa.int32())
values = pa.array([(i * 3) % 1000 for i in range(n)], type=pa.int64())
pq.write_table(pa.table({{"id": ids, "value": values}}), {str(DATA)!r})
print(sum((i * 3) % 1000 for i in range(n)))
print(n)
"""
    out = subprocess.check_output([str(python), "-c", script], text=True)
    expected_sum, expected_count = [int(line) for line in out.splitlines()[-2:]]
    log(f"wrote {DATA} rows={expected_count} sum={expected_sum}")
    return expected_sum, expected_count


def write_coordinator_etc(delta=False):
    etc = SMOKE / "coordinator-etc"
    catalog = etc / "catalog"
    if etc.exists():
        shutil.rmtree(etc)
    catalog.mkdir(parents=True)
    metastore = (SMOKE / "hive-metastore").resolve()
    if metastore.exists():
        shutil.rmtree(metastore)
    metastore.mkdir()
    (etc / "node.properties").write_text(
        "node.environment=smoke\n"
        "node.id=ffffffff-ffff-ffff-ffff-ffffffffffff\n"
        f"node.data-dir={SMOKE / 'coordinator-data'}\n"
    )
    (etc / "jvm.config").write_text(
        "\n".join(
            [
                "-server",
                "-Xmx4G",
                "-XX:+UseG1GC",
                "-XX:+ExitOnOutOfMemoryError",
                "-Djdk.attach.allowAttachSelf=true",
                "--add-opens=java.base/java.io=ALL-UNNAMED",
                "--add-opens=java.base/java.lang=ALL-UNNAMED",
                "--add-opens=java.base/java.lang.ref=ALL-UNNAMED",
                "--add-opens=java.base/java.lang.reflect=ALL-UNNAMED",
                "--add-opens=java.base/java.net=ALL-UNNAMED",
                "--add-opens=java.base/java.nio=ALL-UNNAMED",
                "--add-opens=java.base/java.security=ALL-UNNAMED",
                "--add-opens=java.base/javax.security.auth=ALL-UNNAMED",
                "--add-opens=java.base/javax.security.auth.login=ALL-UNNAMED",
                "--add-opens=java.base/java.text=ALL-UNNAMED",
                "--add-opens=java.base/java.util=ALL-UNNAMED",
                "--add-opens=java.base/java.util.concurrent=ALL-UNNAMED",
                "--add-opens=java.base/java.util.concurrent.atomic=ALL-UNNAMED",
                "--add-opens=java.base/java.util.regex=ALL-UNNAMED",
                "--add-opens=java.base/jdk.internal.loader=ALL-UNNAMED",
                "--add-opens=java.base/sun.security.action=ALL-UNNAMED",
                "--add-opens=java.security.jgss/sun.security.krb5=ALL-UNNAMED",
                "",
            ]
        )
    )
    (etc / "config.properties").write_text(
        "\n".join(
            [
                "coordinator=true",
                "node-scheduler.include-coordinator=false",
                "discovery-server.enabled=true",
                f"discovery.uri=http://127.0.0.1:{COORDINATOR_PORT}",
                f"http-server.http.port={COORDINATOR_PORT}",
                "native-execution-enabled=true",
                "optimizer.optimize-hash-generation=false",
                "regex-library=RE2J",
                "offset-clause-enabled=true",
                "inline-sql-functions=false",
                "use-alternative-function-signatures=true",
                "single-node-execution-enabled=true",
                "query-manager.required-workers=1",
                "query-manager.required-workers-max-wait=30s",
                "",
            ]
        )
    )
    (etc / "log.properties").write_text("com.facebook.presto=INFO\n")
    (catalog / "hive.properties").write_text(
        "\n".join(
            [
                "connector.name=hive-hadoop2",
                "hive.metastore=file",
                f"hive.metastore.catalog.dir={metastore.as_uri()}",
                "hive.allow-drop-table=true",
                "hive.non-managed-table-writes-enabled=true",
                "hive.parquet.use-column-names=true",
                "hive.storage-format=PARQUET",
                "",
            ]
        )
    )
    if delta:
        (catalog / "delta.properties").write_text(
            "\n".join(
                [
                    "connector.name=delta",
                    "hive.metastore=file",
                    f"hive.metastore.catalog.dir={metastore.as_uri()}",
                    "",
                ]
            )
        )
    return etc


def write_worker_etc(cudf, presto_version, delta=False):
    etc = SMOKE / "worker-etc"
    catalog = etc / "catalog"
    if etc.exists():
        shutil.rmtree(etc)
    catalog.mkdir(parents=True)
    (etc / "node.properties").write_text(
        "node.environment=smoke\n"
        "node.internal-address=127.0.0.1\n"
        "node.location=smoke\n"
    )
    # A version mismatch makes the coordinator keep the worker inactive.
    lines = [
        "coordinator=false",
        f"discovery.uri=http://127.0.0.1:{COORDINATOR_PORT}",
        f"presto.version={presto_version}",
        f"http-server.http.port={WORKER_PORT}",
        "shutdown-onset-sec=1",
        "runtime-metrics-collection-enabled=false",
        "use-mmap-allocator=false",
    ]
    if cudf:
        lines.extend(
            [
                "cudf.enabled=true",
                "cudf.allow_cpu_fallback=false",
                "cudf.debug_enabled=true",
            ]
        )
    (etc / "config.properties").write_text("\n".join(lines) + "\n")
    (catalog / "hive.properties").write_text("connector.name=hive-hadoop2\n")
    if delta:
        (catalog / "delta.properties").write_text("connector.name=delta\n")
    return etc


def launcher_args(etc):
    data = SMOKE / "coordinator-data"
    log_dir = SMOKE / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return [
        str(LAUNCHER),
        "--etc-dir",
        str(etc),
        "--data-dir",
        str(data),
        "--pid-file",
        str(SMOKE / "coordinator.pid"),
        "--launcher-log-file",
        str(log_dir / "launcher.log"),
        "--server-log-file",
        str(log_dir / "coordinator.log"),
    ]


def stop_coordinator():
    if not LAUNCHER.exists():
        return
    env = java_env()
    etc = SMOKE / "coordinator-etc"
    if not etc.exists():
        return
    subprocess.run(
        launcher_args(etc) + ["stop"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def start_coordinator(etc, env):
    log("starting coordinator")
    # The launcher symlinks etc/ into the data directory before it creates
    # that directory, so the data directory has to exist first.
    (SMOKE / "coordinator-data").mkdir(parents=True, exist_ok=True)
    stop_coordinator()
    result = subprocess.run(launcher_args(etc) + ["start"], env=env, text=True, capture_output=True)
    if result.returncode != 0:
        raise SystemExit(
            f"coordinator launcher failed\n{result.stdout}\n{result.stderr}\n"
            f"{tail(SMOKE / 'logs' / 'coordinator.log')}"
        )
    deadline = time.time() + 120
    url = f"http://127.0.0.1:{COORDINATOR_PORT}/v1/info"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                body = response.read().decode()
            compact = body.replace(" ", "")
            if '"coordinator":true' in compact and '"starting":false' in compact:
                version = json.loads(body)["nodeVersion"]["version"]
                log(f"coordinator ready on {COORDINATOR_PORT} version={version}")
                return version
        except Exception:
            time.sleep(1)
    raise SystemExit(f"coordinator did not start\n{tail(SMOKE / 'logs' / 'coordinator.log')}")


def stop_worker():
    global worker_proc
    if worker_proc is None or worker_proc.poll() is not None:
        worker_proc = None
        return
    os.killpg(worker_proc.pid, signal.SIGTERM)
    try:
        worker_proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        os.killpg(worker_proc.pid, signal.SIGKILL)
        worker_proc.wait(timeout=10)
    worker_proc = None


def start_worker(cudf, presto_version, delta=False, catalog_extra=""):
    global worker_proc
    stop_worker()
    etc = write_worker_etc(cudf, presto_version, delta)
    if catalog_extra:
        catalog = etc / "catalog" / ("delta.properties" if delta else "hive.properties")
        catalog.write_text(catalog.read_text() + catalog_extra)
    log_path = SMOKE / "logs" / f"worker-{'cudf' if cudf else 'cpu'}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["LD_LIBRARY_PATH"] = library_path()
    log(f"starting {'cuDF' if cudf else 'CPU'} worker")
    with open(log_path, "w") as log_file:
        worker_proc = subprocess.Popen(
            [str(WORKER_BIN), f"--etc_dir={etc}", "--logtostderr=1"],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
    deadline = time.time() + 90
    while time.time() < deadline:
        if worker_proc.poll() is not None:
            raise SystemExit(f"worker exited early\n{tail(log_path)}")
        text = log_path.read_text(errors="replace")
        if "Announcement succeeded" in text:
            registered = "cuDF is registered." in text
            if cudf and not registered:
                raise SystemExit(f"cuDF worker did not register cuDF\n{tail(log_path)}")
            if not cudf and registered:
                raise SystemExit(f"CPU worker registered cuDF\n{tail(log_path)}")
            log(f"worker announced cudf_registered={int(registered)}")
            return log_path
        time.sleep(0.5)
    raise SystemExit(f"worker did not announce\n{tail(log_path)}")


def tail(path, lines=80):
    if not path.exists():
        return ""
    return "\n".join(path.read_text(errors="replace").splitlines()[-lines:])


def presto(env, sql):
    deadline = time.time() + 90
    last = ""
    while time.time() < deadline:
        result = subprocess.run(
            [str(CLI), "--server", f"127.0.0.1:{COORDINATOR_PORT}", "--output-format", "CSV", "--execute", sql],
            env=env,
            text=True,
            capture_output=True,
        )
        last = f"{result.stdout}\n{result.stderr}"
        if result.returncode == 0:
            return result.stdout
        if "still initializing" not in last:
            raise SystemExit(f"SQL failed: {sql}\n{last}")
        time.sleep(1)
    raise SystemExit(f"coordinator stayed initializing: {sql}\n{last}")


def worker_count():
    url = f"http://127.0.0.1:{COORDINATOR_PORT}/v1/service/presto"
    with urllib.request.urlopen(url, timeout=5) as response:
        body = json.loads(response.read().decode())
    count = 0
    for service in body.get("services", []):
        properties = service.get("properties", {})
        if str(properties.get("coordinator", "false")).lower() != "true":
            count += 1
    return count


def wait_for_workers(expected):
    deadline = time.time() + 90
    last = None
    while time.time() < deadline:
        try:
            last = worker_count()
        except Exception:
            time.sleep(1)
            continue
        if last == expected:
            log(f"native workers visible: {last}")
            return
        time.sleep(1)
    raise SystemExit(f"expected {expected} native workers, last count was {last}")


def setup_table(env):
    location = DATA.parent.resolve().as_uri()
    statements = [
        "CREATE SCHEMA IF NOT EXISTS hive.smoke",
        "DROP TABLE IF EXISTS hive.smoke.numbers",
        (
            "CREATE TABLE hive.smoke.numbers (id integer, value bigint) "
            f"WITH (format = 'PARQUET', external_location = '{location}')"
        ),
    ]
    for statement in statements:
        log(statement)
        presto(env, statement)


def query_sum(env, expected_sum, expected_count, sql="SELECT sum(value), count(id) FROM hive.smoke.numbers"):
    log(sql)
    output = presto(env, sql).strip()
    log(f"sql_output={output}")
    rows = list(csv.reader(io.StringIO(output)))
    if len(rows) != 1 or len(rows[0]) != 2:
        raise SystemExit(f"unexpected SQL result: {output}")
    got_sum, got_count = int(rows[0][0]), int(rows[0][1])
    if got_sum != expected_sum or got_count != expected_count:
        raise SystemExit(
            f"result {got_sum}, {got_count} != expected {expected_sum}, {expected_count}"
        )
    return output


def gpu_snapshot():
    try:
        return subprocess.check_output(["nvidia-smi"], text=True, stderr=subprocess.STDOUT, timeout=30)
    except subprocess.CalledProcessError as exc:
        return exc.output or str(exc)


def main():
    for path in (LAUNCHER, CLI, WORKER_BIN):
        if not path.exists():
            raise SystemExit(f"missing {path}")
    env = java_env()
    python = ensure_venv()
    expected_sum, expected_count = write_parquet(python)
    etc = write_coordinator_etc()
    (SMOKE / "logs").mkdir(parents=True, exist_ok=True)
    try:
        presto_version = start_coordinator(etc, env)
        start_worker(False, presto_version)
        wait_for_workers(1)
        setup_table(env)
        cpu_output = query_sum(env, expected_sum, expected_count)
        stop_worker()
        wait_for_workers(0)
        worker_log = start_worker(True, presto_version)
        wait_for_workers(1)
        cudf_output = query_sum(env, expected_sum, expected_count)
        gpu = gpu_snapshot()
        worker_text = worker_log.read_text(errors="replace")
        gpu_used = "presto_server" in gpu or str(worker_proc.pid) in gpu
        cudf_ran = "cuDF is registered." in worker_text and "CudfReduce" in worker_text
        log("----- smoke summary -----")
        log(f"sql=SELECT sum(value), count(id) FROM hive.smoke.numbers")
        log(f"cpu_output={cpu_output}")
        log(f"cudf_output={cudf_output}")
        log(f"gpu_process_visible={int(gpu_used)}")
        log(f"cudf_operator_log={int(cudf_ran)}")
        log(gpu.rstrip())
        if not gpu_used:
            raise SystemExit("nvidia-smi did not show presto_server after the cuDF query")
        if not cudf_ran:
            raise SystemExit(f"cuDF worker log did not show GPU execution\n{tail(worker_log)}")
        log("smoke_ok=1")
    finally:
        stop_worker()
        stop_coordinator()


if __name__ == "__main__":
    main()
