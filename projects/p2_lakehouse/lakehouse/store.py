"""Where tables live: local Delta folders, or Unity Catalog tables on Databricks.

The pipeline code never builds a table name itself. It asks the ``TableStore``
for ``store.name("silver", "transactions")`` and gets either

* locally: ``delta.`/path/lakehouse/silver/transactions```  (a Delta folder), or
* on Databricks: ``workspace.ledgerline_silver.transactions`` (catalog.schema.table).

That one switch is what lets the same PySpark and SQL run on a laptop, in CI
and on Databricks Free Edition.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from pyspark.sql import DataFrame, SparkSession

LAYERS = ("bronze", "silver", "gold", "audit")


def on_databricks() -> bool:
    return "DATABRICKS_RUNTIME_VERSION" in os.environ


def get_spark(app_name: str = "ledgerline-lakehouse") -> SparkSession:
    """Return the Databricks session when running there, else a small local Spark with Delta enabled."""
    active = SparkSession.getActiveSession()
    if active is not None:
        return active
    if on_databricks():
        return SparkSession.builder.getOrCreate()
    from delta import configure_spark_with_delta_pip

    builder = (
        SparkSession.builder.appName(app_name)
        .master(os.environ.get("LAKEHOUSE_SPARK_MASTER", "local[2]"))
        .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
        .config("spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog")
        .config("spark.sql.shuffle.partitions", "4")
        .config("spark.default.parallelism", "4")
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.ui.enabled", "false")
        .config("spark.ui.showConsoleProgress", "false")
        .config("spark.databricks.delta.snapshotPartitions", "2")
    )
    spark = configure_spark_with_delta_pip(builder).getOrCreate()
    spark.sparkContext.setLogLevel("ERROR")
    return spark


@dataclass(frozen=True)
class LakehouseConfig:
    """``root`` is a local folder; ``catalog`` and ``schema_prefix`` are used on Databricks."""

    landing: str
    root: str | None = None
    catalog: str | None = None
    schema_prefix: str = "ledgerline"

    @property
    def is_catalog(self) -> bool:
        return self.catalog is not None

    @classmethod
    def local(cls, workdir: str | Path) -> LakehouseConfig:
        workdir = Path(workdir).resolve()
        return cls(landing=str(workdir / "landing"), root=str(workdir / "lakehouse"))

    @classmethod
    def databricks(cls, catalog: str = "workspace", schema_prefix: str = "ledgerline") -> LakehouseConfig:
        return cls(landing=f"/Volumes/{catalog}/{schema_prefix}_bronze/landing", catalog=catalog,
                   schema_prefix=schema_prefix)


class TableStore:
    def __init__(self, spark: SparkSession, config: LakehouseConfig) -> None:
        self.spark = spark
        self.config = config

    def schema(self, layer: str) -> str:
        return f"{self.config.catalog}.{self.config.schema_prefix}_{layer}"

    def path(self, layer: str, table: str) -> str:
        return f"{self.config.root}/{layer}/{table}"

    def name(self, layer: str, table: str) -> str:
        """SQL identifier usable in FROM clauses and MERGE statements."""
        if self.config.is_catalog:
            return f"{self.schema(layer)}.{table}"
        return f"delta.`{self.path(layer, table)}`"

    def setup(self) -> None:
        """Create Unity Catalog schemas and the landing volume (no-op locally)."""
        if not self.config.is_catalog:
            return
        for layer in LAYERS:
            self.spark.sql(f"CREATE SCHEMA IF NOT EXISTS {self.schema(layer)} "
                           f"COMMENT 'Ledgerline Bank synthetic lakehouse: {layer} layer'")
        self.spark.sql(f"CREATE VOLUME IF NOT EXISTS {self.schema('bronze')}.landing "
                       "COMMENT 'Landing zone for synthetic source extracts'")

    def exists(self, layer: str, table: str) -> bool:
        if self.config.is_catalog:
            return self.spark.catalog.tableExists(self.name(layer, table))
        return Path(self.path(layer, table), "_delta_log").exists()

    def read(self, layer: str, table: str, version: int | None = None) -> DataFrame:
        if version is None:
            return self.spark.table(self.name(layer, table))
        return self.spark.sql(f"SELECT * FROM {self.name(layer, table)} VERSION AS OF {version}")

    def write(self, df: DataFrame, layer: str, table: str, mode: str = "overwrite", comment: str | None = None,
              change_feed: bool = False) -> None:
        writer = df.write.format("delta").mode(mode)
        if mode == "overwrite":
            writer = writer.option("overwriteSchema", "true")
        if change_feed:
            # Change Data Feed records row-level inserts, updates and deletes for downstream incremental reads.
            writer = writer.option("delta.enableChangeDataFeed", "true")
        if self.config.is_catalog:
            writer.saveAsTable(self.name(layer, table))
            if comment:
                self.spark.sql(f"COMMENT ON TABLE {self.name(layer, table)} IS '{comment}'")
        else:
            writer.save(self.path(layer, table))

    def delta_table(self, layer: str, table: str):
        from delta.tables import DeltaTable

        if self.config.is_catalog:
            return DeltaTable.forName(self.spark, self.name(layer, table))
        return DeltaTable.forPath(self.spark, self.path(layer, table))

    def version(self, layer: str, table: str) -> int:
        return int(self.delta_table(layer, table).history(1).collect()[0]["version"])

    def history(self, layer: str, table: str) -> DataFrame:
        return self.delta_table(layer, table).history()

    def changes(self, layer: str, table: str, starting_version: int) -> DataFrame:
        """Row-level changes since a version, from the Delta Change Data Feed."""
        reader = self.spark.read.format("delta").option("readChangeFeed", "true") \
            .option("startingVersion", starting_version)
        if self.config.is_catalog:
            return reader.table(self.name(layer, table))
        return reader.load(self.path(layer, table))
