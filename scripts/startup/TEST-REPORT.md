# Test report

Run on this machine on 2026-10-08. Coordinator is Presto `0.300-SNAPSHOT-1a2fbbe` with Delta Kernel 4.4.1. Worker is Prestissimo with cuDF, Parquet, and, for the cloud-table runs, Velox S3. SQL went through the Java coordinator.

A pass means the printed result matched the expected row. GPU checks also required `presto_server` in `nvidia-smi` and a cuDF operator in the worker log.

## Passed

| Test | SQL | Result | What it showed |
| --- | --- | --- | --- |
| Parquet, CPU and cuDF | `sum(value), count(id)` on `hive.smoke.numbers`, 100,000 rows | `"49950000","100000"` | Coordinator plus native worker. cuDF run logged `CudfReduce` and `nvidia-smi` showed `presto_server`. |
| Ordinary Delta, CPU and cuDF | same sum and count on `delta."$path$"."file:///home/nvidia/presto-delta-smoke/delta-numbers"` | `"49950000","100000"` | This build reads a normal Delta folder. `delta_smoke_ok=1`. |
| Ordinary Delta join, CPU and cuDF | inner join of the two local Delta folders | `"4950000","10000"` | `delta_join_ok=1`. cuDF run logged `CudfHashJoinProbe`. |
| Unity Catalog file table, CPU | same join on `unity.smoke.join_left` and `join_right` | `"4950000","10000"` | Query `20261008_050815_00000_urgh8`. Tables are `MANAGED` and `catalogManaged`. Coordinator called Unity Catalog and read `storage_location`. |
| Unity Catalog file table, cuDF | same join | `"4950000","10000"` | Query `20261008_050847_00001_urgh8`. Worker log contains `CudfHashJoinProbe`. |
| Unity Catalog cloud table, CPU | `sum(value), count(id)` on `delta.smoke.cloud_numbers`, 10 rows | `"135","10"` | Query `20261008_185714_00000_nqqzn`. `cloud_query_ok=1`. Coordinator stored the temporary key and logged `Using Unity Catalog temporary credentials`. Worker read `s3://warehouse` with the local bucket password from its catalog. |

The cloud-table pass used a freshly vended one-hour key. Planning took 779ms, the query ran for 96ms.

## Failed, then fixed

| Test | Result | Why it failed |
| --- | --- | --- |
| First Parquet SQL, query `20261008_004916_00000_75bme` | Fail | The worker announced itself, but the coordinator did not treat that announcement as an active worker. The query waited 300 seconds and never ran. |
| cuDF Parquet, query `20261008_010403_00004_knc5b` | Fail | `cudf.allow_cpu_fallback=false` and the plan still had an operator with no cuDF replacement. The same query later passed with `single-node-execution-enabled=true`. |
| First Delta `$path$` query | Fail | Presto lowercased the path, so `/home/nvidia/Presto/...` became `/home/nvidia/presto/...` and the table was not found. The tables were moved to an all-lowercase directory. |
| Unity Catalog file join, query `20261008_041444_00000_jv49e` | Fail | Delta Kernel 4.0.0 refused the table: `Unsupported Delta table feature: catalogManaged`. |
| Same join after installing Kernel 4.4.1 | Fail, same message | `plugin/delta` still contained `delta-kernel-*-4.0.0.jar`. The plugin loader sorts jar names and loads the older jar first. Deleting the 4.0.0 jars made the 05:08 queries pass. The expected `maxCatalogVersion` error did not appear, because this connector opens the table with `Table.forPath`. |
| Cloud table, query `20261008_062104_00000_ckqa9` | Fail | A logging proxy on port 8083 sat in front of Unity Catalog. Listing schemas hit the 30 second timeout. Planning never reached S3. The query now talks to Unity Catalog on port 8082. |
| Cloud table, query `20261008_062844_00000_bcif7` | Fail | The coordinator had the `s3://` folder and no endpoint or key, so it called public AWS in `us-east-1` for about 45 seconds. Nothing was scheduled. |
| Cloud table, query `20261008_064354_00000_us9d6` | Fail | The coordinator used the temporary key and sent a Parquet split. The worker stopped with `No registered file system matched` for that `s3://` file. The worker binary did not have Velox S3 yet. |
| First S3 worker rebuild, 14:28 UTC | Not a working S3 worker | `PRESTO_ENABLE_S3` turned on, and `VELOX_ENABLE_S3` stayed off, so the S3 filesystem file was still an empty stub. The next rebuild passed `-DVELOX_ENABLE_S3=ON` and finished at 15:04 UTC. |
| Cloud table, query `20261008_184722_00000_7gfiz` | Fail | The coordinator used the Unity Catalog key. Planning died in 720ms and the worker never received a task. The key had been minted around 06:18 UTC with a one-hour lifetime, so it was expired. |

## Failed on purpose

| Test | Result | Why |
| --- | --- | --- |
| Cloud table with a fake Unity Catalog key, query `20261008_191831_00000_dheny` | Fail | Unity Catalog handed the coordinator a fake access key, secret, and session token. The coordinator logged that it used those temporary credentials. Planning stopped after 1128ms. The worker still had the real local bucket password and never got the query. The real key was put back afterward. |

The coordinator does use the key Unity Catalog gives it. A bad key stops the query before the worker runs.

## Not run

- The cloud table was not run with the cuDF worker.
- The worker was not given the Unity Catalog session token. It used the local bucket password in its catalog file.
- No `UPDATE`, `DELETE`, or `MERGE`, so deletion vectors were not tested. The file tables had none.
- No unbackfilled Unity Catalog commit. After Spark writes the Delta log, this connector reads that log and does not call the commits API.
- Presto was not asked to create or modify a Unity Catalog managed table.
- ADLS and GCS were not tested.
