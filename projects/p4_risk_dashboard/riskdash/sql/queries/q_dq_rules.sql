-- Data-quality rule catalogue.
SELECT rule_id, table_name, column_name, dimension, description, severity, threshold_pct, owner, origin
FROM ${mart.dq_rules}
ORDER BY rule_id
