"""CapitalFlow 360 lakehouse: Excel -> Parquet landing -> Bronze -> Silver -> Gold (Delta Lake).

The same functions run locally (pyspark + delta-spark, path-based tables) and on Databricks
(serverless jobs, Unity Catalog tables). Only the ``Store`` differs.
"""
