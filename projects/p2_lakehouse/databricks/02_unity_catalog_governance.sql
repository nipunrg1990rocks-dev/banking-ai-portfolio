-- Databricks notebook source
-- MAGIC %md
-- MAGIC # Unity Catalog governance on the Ledgerline tables
-- MAGIC Run after `00_run_pipeline`. Shows table/column documentation, tags, a column mask that hides
-- MAGIC customer names from anyone outside an approved group, and a view of lineage-ready Gold tables.
-- MAGIC Uses catalog `workspace`; change it below if you used another.

-- COMMAND ----------

USE CATALOG workspace;

-- COMMAND ----------

-- Document sensitive columns and tag them so they can be found by classification.
ALTER TABLE ledgerline_silver.customers ALTER COLUMN first_name COMMENT 'PII: customer given name (synthetic)';
ALTER TABLE ledgerline_silver.customers ALTER COLUMN last_name COMMENT 'PII: customer family name (synthetic)';
ALTER TABLE ledgerline_silver.customers ALTER COLUMN email_hash COMMENT 'SHA-256 of the e-mail address; raw e-mail is never stored';
ALTER TABLE ledgerline_silver.customers ALTER COLUMN first_name SET TAGS ('classification' = 'pii');
ALTER TABLE ledgerline_silver.customers ALTER COLUMN last_name SET TAGS ('classification' = 'pii');
ALTER TABLE ledgerline_gold.aml_alert_candidates SET TAGS ('domain' = 'financial_crime', 'review' = 'human_required');

-- COMMAND ----------

-- Column mask: only members of the 'ledgerline_kyc_analysts' group see real names.
CREATE OR REPLACE FUNCTION ledgerline_silver.mask_name(name STRING)
  RETURN CASE WHEN is_account_group_member('ledgerline_kyc_analysts') THEN name ELSE '*****' END;

ALTER TABLE ledgerline_silver.customers ALTER COLUMN first_name SET MASK ledgerline_silver.mask_name;
ALTER TABLE ledgerline_silver.customers ALTER COLUMN last_name SET MASK ledgerline_silver.mask_name;

-- COMMAND ----------

-- You are not in that group, so names now show as *****.
SELECT customer_id, first_name, last_name, kyc_risk_rating, is_current
FROM ledgerline_silver.customers
LIMIT 10;

-- COMMAND ----------

-- MAGIC %md Open **Catalog > workspace > ledgerline_gold > customer_360 > Lineage** to see the
-- MAGIC upstream Silver tables Unity Catalog captured automatically from the pipeline run.
