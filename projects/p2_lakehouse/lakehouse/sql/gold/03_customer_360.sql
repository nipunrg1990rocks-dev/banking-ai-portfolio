-- One row per current customer: profile, product holdings, recent activity and alerts.
-- Names, e-mail and phone are deliberately left out (data minimisation in Gold).
WITH as_of AS (
  SELECT MAX(txn_date) AS as_of_date FROM ${silver.transactions}
),
accts AS (
  SELECT
    customer_id,
    COUNT(*)                                            AS accounts_total,
    SUM(CASE WHEN status = 'ACTIVE' THEN 1 ELSE 0 END)  AS accounts_active,
    CONCAT_WS(',', ARRAY_SORT(COLLECT_SET(product)))    AS products
  FROM ${silver.accounts}
  GROUP BY customer_id
),
activity AS (
  SELECT
    t.customer_id,
    COUNT(*)                                                        AS txn_count_30d,
    ROUND(SUM(CASE WHEN direction = 'CREDIT' THEN amount_usd END), 2) AS inflow_usd_30d,
    ROUND(SUM(CASE WHEN direction = 'DEBIT' THEN amount_usd END), 2)  AS outflow_usd_30d,
    ROUND(SUM(CASE WHEN txn_type LIKE 'CASH%' THEN amount_usd END), 2) AS cash_usd_30d,
    ROUND(SUM(CASE WHEN counterparty_country <> 'US' THEN amount_usd END), 2) AS cross_border_usd_30d,
    MAX(txn_date)                                                   AS last_txn_date
  FROM ${silver.transactions} t CROSS JOIN as_of
  WHERE t.txn_date > as_of.as_of_date - INTERVAL 30 DAYS
  GROUP BY t.customer_id
),
alerts AS (
  SELECT customer_id, COUNT(*) AS open_alerts, CONCAT_WS(',', ARRAY_SORT(COLLECT_SET(rule_code))) AS alert_rules
  FROM ${gold.aml_alert_candidates}
  GROUP BY customer_id
),
versions AS (
  SELECT customer_id, COUNT(*) - 1 AS profile_changes
  FROM ${silver.customers}
  GROUP BY customer_id
)
SELECT
  c.customer_id,
  c.segment,
  c.country,
  c.kyc_risk_rating,
  c.pep_flag,
  c.branch_code,
  b.region,
  c.onboarding_date,
  YEAR(c.date_of_birth)                    AS birth_year,
  SIZE(c.dq_warnings) > 0                  AS has_dq_warnings,
  v.profile_changes,
  COALESCE(a.accounts_total, 0)            AS accounts_total,
  COALESCE(a.accounts_active, 0)           AS accounts_active,
  a.products,
  COALESCE(x.txn_count_30d, 0)             AS txn_count_30d,
  COALESCE(x.inflow_usd_30d, 0)            AS inflow_usd_30d,
  COALESCE(x.outflow_usd_30d, 0)           AS outflow_usd_30d,
  COALESCE(x.cash_usd_30d, 0)              AS cash_usd_30d,
  COALESCE(x.cross_border_usd_30d, 0)      AS cross_border_usd_30d,
  x.last_txn_date,
  COALESCE(al.open_alerts, 0)              AS open_alerts,
  al.alert_rules
FROM ${silver.customers} c
LEFT JOIN ${silver.ref_branches} b ON b.branch_code = c.branch_code
LEFT JOIN accts a     ON a.customer_id = c.customer_id
LEFT JOIN activity x  ON x.customer_id = c.customer_id
LEFT JOIN alerts al   ON al.customer_id = c.customer_id
LEFT JOIN versions v  ON v.customer_id = c.customer_id
WHERE c.is_current
