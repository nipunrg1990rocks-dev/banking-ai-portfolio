-- One row per day x data-quality rule. A rule "meets" its threshold when its pass rate is at
-- or above the rule's threshold. Severity weights: CRITICAL 3, HIGH 2, MEDIUM 1.
WITH scored AS (
  SELECT
    r.run_date                                                 AS obs_date,
    q.rule_id,
    q.table_name,
    q.column_name,
    q.dimension,
    q.severity,
    q.owner,
    r.rows_checked,
    r.rows_failed,
    100.0 * (r.rows_checked - r.rows_failed) / r.rows_checked  AS pass_rate,
    q.threshold_pct,
    CASE q.severity WHEN 'CRITICAL' THEN 3 WHEN 'HIGH' THEN 2 ELSE 1 END AS weight
  FROM ${mart.dq_results} r
  JOIN ${mart.dq_rules} q ON q.rule_id = r.rule_id
)
SELECT
  obs_date, rule_id, table_name, column_name, dimension, severity, owner,
  rows_checked, rows_failed,
  ROUND(pass_rate, 3)                                          AS pass_rate_pct,
  threshold_pct,
  weight,
  CASE WHEN pass_rate >= threshold_pct THEN weight ELSE 0 END  AS weight_met,
  CASE WHEN pass_rate <  threshold_pct THEN 1 ELSE 0 END       AS breached
FROM scored
