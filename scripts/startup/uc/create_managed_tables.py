#!/usr/bin/env python3
"""Create two Unity Catalog managed Delta tables with Spark.

Spark registers the tables with Unity Catalog and writes the rows.
Presto reads them afterward through the Unity Catalog lookup.
"""

import os

from pyspark.sql import SparkSession

uc_url = os.environ.get("UC_URL", "http://127.0.0.1:8082")
catalog = os.environ.get("UC_CATALOG", "unity")

spark = (
    SparkSession.builder.appName("uc-managed-delta")
    .config(
        "spark.jars.packages",
        "io.unitycatalog:unitycatalog-spark_4.1_2.13:0.5.0,io.delta:delta-spark_4.1_2.13:4.3.0",
    )
    .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
    .config("spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog")
    .config(f"spark.sql.catalog.{catalog}", "io.unitycatalog.spark.UCSingleCatalog")
    .config(f"spark.sql.catalog.{catalog}.uri", uc_url)
    .config(f"spark.sql.catalog.{catalog}.token", "")
    .config("spark.sql.defaultCatalog", catalog)
    .config("spark.driver.memory", "4g")
    .config("spark.ui.enabled", "false")
    .master("local[4]")
    .getOrCreate()
)
spark.sparkContext.setLogLevel("WARN")

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {catalog}.smoke")
spark.range(0, 100000).createOrReplaceTempView("nums")
for name, columns, select in (
    (
        "join_left",
        "id INT, value BIGINT",
        "SELECT CAST(id AS INT) AS id, CAST((id * 3) % 1000 AS BIGINT) AS value FROM nums",
    ),
    (
        "join_right",
        "id INT",
        "SELECT CAST(id AS INT) AS id FROM nums WHERE id % 10 = 0",
    ),
):
    spark.sql(f"DROP TABLE IF EXISTS {catalog}.smoke.{name}")
    spark.sql(
        f"CREATE TABLE {catalog}.smoke.{name} ({columns}) "
        "USING DELTA TBLPROPERTIES ('delta.feature.catalogManaged' = 'supported')"
    )
    spark.sql(f"INSERT INTO {catalog}.smoke.{name} {select}")
    print(f"----- DESCRIBE EXTENDED {catalog}.smoke.{name} -----")
    spark.sql(f"DESCRIBE EXTENDED {catalog}.smoke.{name}").show(200, truncate=False)

spark.stop()
print("managed tables created")
