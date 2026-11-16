"""Where tables live: Delta paths locally, Unity Catalog tables on Databricks."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class Store:
    landing: str            # landing zone root (Parquet files)
    checkpoints: str        # streaming checkpoint root
    tables: str             # local: directory of Delta tables; Databricks: "catalog.schema"
    mode: str = "path"      # "path" (local) or "catalog" (Databricks Unity Catalog)

    def name(self, table: str) -> str:
        return str(Path(self.tables) / table) if self.mode == "path" else f"{self.tables}.{table}"

    def read(self, spark, table: str):
        return spark.read.format("delta").load(self.name(table)) if self.mode == "path" else spark.table(self.name(table))

    def exists(self, spark, table: str) -> bool:
        if self.mode == "path":
            return (Path(self.name(table)) / "_delta_log").exists()
        return spark.catalog.tableExists(self.name(table))

    def write(self, df, table: str, mode: str = "overwrite") -> None:
        w = df.write.format("delta").mode(mode).option("overwriteSchema", "true" if mode == "overwrite" else "false")
        if self.mode == "path":
            w.save(self.name(table))
        else:
            w.saveAsTable(self.name(table))

    def delta(self, spark, table: str):
        from delta.tables import DeltaTable
        return DeltaTable.forPath(spark, self.name(table)) if self.mode == "path" else DeltaTable.forName(spark, self.name(table))

    def checkpoint(self, name: str) -> str:
        return f"{self.checkpoints.rstrip('/')}/{name}"
