-- Data-quality scorecard for the latest pipeline run: one row per rule and table.
WITH latest AS (
  SELECT MAX_BY(run_id, run_started_at) AS run_id FROM ${audit.dq_results}
)
SELECT
  r.run_id,
  r.layer,
  r.table_name,
  r.rule_name,
  r.action,
  r.description,
  SUM(r.rows_checked)                                   AS rows_checked,
  SUM(r.rows_failed)                                    AS rows_failed,
  ROUND(100 * (1 - SUM(r.rows_failed) / GREATEST(SUM(r.rows_checked), 1)), 3) AS pass_rate_pct,
  CASE
    WHEN SUM(r.rows_failed) = 0 THEN 'PASS'
    WHEN r.action = 'warn'      THEN 'WARN'
    ELSE 'QUARANTINED'
  END                                                   AS status
FROM ${audit.dq_results} r JOIN latest USING (run_id)
GROUP BY r.run_id, r.layer, r.table_name, r.rule_name, r.action, r.description
