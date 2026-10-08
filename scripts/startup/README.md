# Startup scripts

These scripts build this checkout, start a Java coordinator with a Prestissimo worker, and run SQL smoke tests. Every path is absolute and points at this machine:

| Path | What it is |
| --- | --- |
| `/home/nvidia/Presto` | Working directory, logs, unpacked coordinator, smoke data |
| `/home/nvidia/Presto/presto` | This git checkout |
| `/home/nvidia/Presto/coordinator` | Unpacked `presto-server` launcher |
| `/home/nvidia/Presto/smoke` | Python smoke tests the shell scripts actually run |
| `/home/nvidia/Presto/uc` | Unity Catalog config and the Spark table creator |

The copies under `scripts/startup/` are the versions kept in git. The shell scripts still call `/home/nvidia/Presto/smoke` and `/home/nvidia/Presto/uc`, so edit those live copies when you want a rerun to pick up a change.

Ports: coordinator `8080`, native worker `8081`, Unity Catalog `8082`.

## Order

Run these once, in this order, on a machine that already has CUDA at `/usr/local/cuda`:

```bash
/home/nvidia/Presto/presto/scripts/startup/switch-to-johnzed.sh
/home/nvidia/Presto/presto/scripts/startup/build-prestissimo-cudf.sh
/home/nvidia/Presto/presto/scripts/startup/build-coordinator.sh
```

After both builds succeed, the smoke tests below can run. `rebuild-worker.sh` is only for a later C++ change. `simulate-uc-managed-delta.sh` is the Unity Catalog repro, and it is expected to stop inside the Delta reader.

## switch-to-johnzed.sh

Points the checkout at [JohnZed/presto](https://github.com/JohnZed/presto) branch `delta-lake-cudf` and leaves the Velox submodule on the commit that branch already pins.

```bash
/home/nvidia/Presto/presto/scripts/startup/switch-to-johnzed.sh
```

What it does:

- Adds the `johnzed` remote if it is missing, then fetches `delta-lake-cudf`.
- Stashes uncommitted Presto edits, including untracked files, under the message `local presto edits before johnzed delta-lake-cudf`.
- Checks out local branch `delta-lake-cudf` at that tip. Ignored build output under `presto-native-execution/_build` stays put.
- Resets tracked edits inside the Velox working tree. It does not fetch or reconfigure Velox.
- Reapplies the local `libcares` link line in `presto-native-execution/CMakeLists.txt` when that line is missing. Without it, the worker link fails on undefined `ares_*` symbols from `libproxygen.a`.

Skip this once you are already on `delta-lake-cudf` or on a branch cut from it, such as `delta-lake-cudf-unity`. Running it again replaces the worktree with John's tip.

## build-prestissimo-cudf.sh

Full Prestissimo build with cuDF and Parquet. This is the long first build. It needs `sudo` because `setup-ubuntu.sh` installs apt packages and writes libraries under `/usr/local`.

```bash
/home/nvidia/Presto/presto/scripts/startup/build-prestissimo-cudf.sh
```

Log: `/home/nvidia/Presto/build-prestissimo-cudf.log`.

What it does:

- Requires `nvcc` at `/usr/local/cuda/bin/nvcc` and CMake 4 or newer on `PATH` (`~/.local/bin` from `uv` is fine).
- Checks out Velox from `https://github.com/JohnZed/velox.git` branch `delta-lake-cudf` when that checkout is missing or is not already on the remote tip. An existing checkout that already matches the tip is left alone, including local setup-script edits.
- Sets `submodule.presto-native-execution/velox.update` to `none` so a later `make release` does not reset Velox. This script itself runs `make cmake-and-build`, not `make release`.
- Builds dependencies with Clang 15, then configures and compiles Prestissimo with GCC. The cuDF pin on this Velox branch rejects Clang.
- Passes `PRESTO_OPTIONAL_FEATURES=cudf,parquet`, `CUDA_ARCHITECTURES=native`, and `BUILD_FAISS=false`.

The worker binary is:

```text
/home/nvidia/Presto/presto/presto-native-execution/_build/release/presto_cpp/main/presto_server
```

Use `rebuild-worker.sh` for a later incremental link. Rerun this script only when you need to reconfigure or rebuild dependencies.

## build-coordinator.sh

Builds the Java coordinator from the current checkout and unpacks it to `/home/nvidia/Presto/coordinator`.

```bash
/home/nvidia/Presto/presto/scripts/startup/build-coordinator.sh
```

Log: `/home/nvidia/Presto/build-coordinator.log`.

What it does:

- Uses OpenJDK 17, installing it with `sudo apt-get` when it is missing.
- Runs `./mvnw -T 8 install -DskipTests -pl '!presto-docs'`. It does not run Maven clean, and it does not pass `-Dmaven.test.skip=true`, because other modules need the `presto-common` test jar.
- Unpacks `presto-server/target/presto-server-<version>.tar.gz`, where `<version>` is the `presto-root` version in `pom.xml` (`0.300-SNAPSHOT` on this branch).

Launcher: `/home/nvidia/Presto/coordinator/bin/launcher`. The CLI is the newest `presto-cli-*-executable.jar` under `presto-cli/target/`.

## rebuild-worker.sh

Relinks `presto_server` from the existing Release build. It does not reconfigure CMake, rebuild cuDF, or run Maven.

```bash
/home/nvidia/Presto/presto/scripts/startup/rebuild-worker.sh
```

Run it from a shell after a C++ change in `presto-native-execution`. Success ends with `worker ready: _build/release/presto_cpp/main/presto_server`.

## Smoke tests

Each smoke test starts the coordinator and one native worker, runs the same SQL with cuDF off and then with `cudf.enabled=true` and `cudf.allow_cpu_fallback=false`, and stops both processes when it finishes. The worker's `presto.version` is read from the coordinator at runtime so the two stay in sync.

They create `/home/nvidia/Presto/smoke/.venv` and install the Python packages they need. Coordinator and worker logs land in `/home/nvidia/Presto/smoke/logs`.

Run them with the venv the first script creates, or let each script create it:

```bash
python3 -m venv /home/nvidia/Presto/smoke/.venv
/home/nvidia/Presto/smoke/.venv/bin/python /home/nvidia/Presto/smoke/run_smoke.py
/home/nvidia/Presto/smoke/.venv/bin/python /home/nvidia/Presto/smoke/run_delta_smoke.py
/home/nvidia/Presto/smoke/.venv/bin/python /home/nvidia/Presto/smoke/run_delta_join_smoke.py
```

`run_smoke.py` writes 100,000 synthetic Parquet rows and queries `hive.smoke.numbers`. A pass prints `smoke_ok=1`. Both CPU and cuDF return `"49950000","100000"`. The cuDF run also requires `nvidia-smi` to show `presto_server` and the worker log to contain `CudfReduce`.

`run_delta_smoke.py` writes an ordinary Delta table at `/home/nvidia/presto-delta-smoke/delta-numbers` and queries it through `delta."$path$"`. The directory is all lowercase because the connector lowercases the path. A pass prints `delta_smoke_ok=1` with the same sum and count.

`run_delta_join_smoke.py` writes `/home/nvidia/presto-delta-smoke/join-left` (100,000 rows) and `join-right` (every 10th id) and runs an inner join. A pass prints `delta_join_ok=1`, result `"4950000","10000"`, and the cuDF worker log contains `CudfHashJoinProbe`.

## simulate-uc-managed-delta.sh

Stands up Unity Catalog, creates two catalog-managed Delta tables with Spark, rebuilds the coordinator, and runs the same join through the `unity` metastore. It stops at the first failure and does not change the Delta reader.

```bash
/home/nvidia/Presto/presto/scripts/startup/simulate-uc-managed-delta.sh
```

Log: `/home/nvidia/Presto/uc-simulation.log`.

What it does:

- Requires Docker and OpenJDK 17.
- Starts `unitycatalog/unitycatalog:v0.5.0` as container `presto-uc`, listening on host port `8082`. Table files go to `/home/nvidia/presto-uc/warehouse`. Server settings are `uc/server.properties` (`server.managed-table.enabled=true`, authorization off).
- Installs PySpark 4.1.0 into `/home/nvidia/Presto/smoke/.venv` and runs `uc/create_managed_tables.py`. That creates `unity.smoke.join_left` and `unity.smoke.join_right` with `delta.feature.catalogManaged=supported`.
- Rebuilds and unpacks the coordinator the same way `build-coordinator.sh` does.
- Runs `smoke/run_uc_join.py`, which points the `delta` catalog at `hive.metastore=unity` and `http://127.0.0.1:8082`.

The lookup succeeds and prints `storage_location`. The query then fails in Delta Kernel 4.0.0 while opening that directory:

```text
Unsupported Delta table feature: table requires feature "catalogManaged" which is unsupported by this version of Delta Kernel.
```

That is the end of this simulation. The script does not call the Unity Catalog commits API, and it does not start the cuDF worker after the coordinator fails to open the table.

`uc/create_managed_tables.py` can be rerun on its own while `presto-uc` is already up:

```bash
UC_URL=http://127.0.0.1:8082 UC_CATALOG=unity \
  /home/nvidia/Presto/smoke/.venv/bin/python /home/nvidia/Presto/uc/create_managed_tables.py
```
