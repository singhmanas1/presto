#!/usr/bin/env python3
"""Create one Unity Catalog managed Delta table whose files live on MinIO.

Spark asks Unity Catalog for temporary credentials and writes the table.
Presto is not involved.
"""

import json
import os
import urllib.error
import urllib.request

from pyspark.sql import SparkSession

UC_URL = os.environ.get("UC_URL", "http://127.0.0.1:8082")
CATALOG = os.environ.get("UC_CATALOG", "clouds")
ENDPOINT = os.environ.get("MINIO_ENDPOINT", "http://127.0.0.1:9000")
TABLE = f"{CATALOG}.smoke.cloud_numbers"


def uc_json(method, path, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(
        UC_URL + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode()
            return response.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as error:
        body = error.read().decode(errors="replace")
        try:
            parsed = json.loads(body) if body else {}
        except json.JSONDecodeError:
            parsed = {"raw": body}
        return error.code, parsed


def ensure_catalog():
    status, body = uc_json("GET", f"/api/2.1/unity-catalog/catalogs/{CATALOG}")
    if status == 404:
        status, body = uc_json(
            "POST",
            "/api/2.1/unity-catalog/catalogs",
            {"name": CATALOG, "storage_root": "s3://warehouse"},
        )
        print(f"create catalog status={status}")
    location = str(body.get("storage_root", ""))
    print(f"catalog {CATALOG} storage_root={location}")
    if not location.startswith("s3://"):
        raise SystemExit(f"catalog {CATALOG} is not on s3: {body}")


def redact(value):
    if isinstance(value, dict):
        hidden = {}
        for key, item in value.items():
            if key in {"access_key_id", "secret_access_key", "session_token", "sas_token"}:
                hidden[key] = "***"
            else:
                hidden[key] = redact(item)
        return hidden
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def show_vended_credentials(table):
    table_id = table.get("table_id")
    status, body = uc_json(
        "POST",
        "/api/2.1/unity-catalog/temporary-table-credentials",
        {"table_id": table_id, "operation": "READ"},
    )
    print(f"temporary-table-credentials status={status}")
    print(json.dumps(redact(body), indent=2)[:2000])
    if status != 200:
        status, body = uc_json(
            "GET",
            "/api/2.1/unity-catalog/delta/v1/catalogs/"
            f"{CATALOG}/schemas/smoke/tables/cloud_numbers/credentials?operation=READ",
        )
        print(f"delta credentials status={status}")
        print(json.dumps(redact(body), indent=2)[:2000])


def main():
    ensure_catalog()
    spark = (
        SparkSession.builder.appName("uc-s3-managed-delta")
        .config(
            "spark.jars.packages",
            "io.unitycatalog:unitycatalog-spark_4.1_2.13:0.5.0,"
            "io.delta:delta-spark_4.1_2.13:4.3.0,"
            "org.apache.hadoop:hadoop-aws:3.4.2",
        )
        .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
        .config("spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog")
        .config(f"spark.sql.catalog.{CATALOG}", "io.unitycatalog.spark.UCSingleCatalog")
        .config(f"spark.sql.catalog.{CATALOG}.uri", UC_URL)
        .config(f"spark.sql.catalog.{CATALOG}.token", "")
        .config(f"spark.sql.catalog.{CATALOG}.renewCredential.enabled", "false")
        .config("spark.sql.defaultCatalog", CATALOG)
        .config("spark.hadoop.fs.s3.impl", "org.apache.hadoop.fs.s3a.S3AFileSystem")
        .config("spark.hadoop.fs.s3a.impl", "org.apache.hadoop.fs.s3a.S3AFileSystem")
        .config("spark.hadoop.fs.s3a.endpoint", ENDPOINT)
        .config("spark.hadoop.fs.s3a.path.style.access", "true")
        .config("spark.hadoop.fs.s3a.connection.ssl.enabled", "false")
        .config("spark.hadoop.fs.s3a.endpoint.region", "us-east-1")
        .config("spark.driver.memory", "4g")
        .config("spark.ui.enabled", "false")
        .master("local[4]")
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("WARN")
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.smoke")
    spark.sql(f"DROP TABLE IF EXISTS {TABLE}")
    spark.sql(
        f"CREATE TABLE {TABLE} (id INT, value BIGINT) "
        "USING DELTA TBLPROPERTIES ('delta.feature.catalogManaged' = 'supported')"
    )
    spark.range(0, 10).createOrReplaceTempView("nums")
    spark.sql(
        f"INSERT INTO {TABLE} "
        "SELECT CAST(id AS INT) AS id, CAST((id * 3) % 1000 AS BIGINT) AS value FROM nums"
    )
    spark.stop()

    status, table = uc_json("GET", f"/api/2.1/unity-catalog/tables/{TABLE}")
    location = str(table.get("storage_location", ""))
    print(f"table status={status} storage_location={location}")
    if not location.startswith("s3://"):
        raise SystemExit(f"managed table is not on s3: {table}")
    show_vended_credentials(table)
    print("s3 managed table created")


if __name__ == "__main__":
    main()
