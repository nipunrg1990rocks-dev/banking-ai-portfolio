-- Monthly KPIs by customer segment. Segment is taken "as of" the transaction
-- date from the SCD2 history, so a customer who moved segment in October is
-- counted in their old segment for September.
SELECT
  DATE_TRUNC('MONTH', t.txn_date)                                   AS month,
  c.segment,
  COUNT(DISTINCT t.customer_id)                                     AS active_customers,
  COUNT(*)                                                          AS txn_count,
  ROUND(SUM(t.amount_usd), 2)                                       AS volume_usd,
  ROUND(SUM(CASE WHEN t.direction = 'CREDIT' THEN t.amount_usd ELSE 0 END), 2) AS inflow_usd,
  ROUND(SUM(CASE WHEN t.direction = 'DEBIT' THEN t.amount_usd ELSE 0 END), 2)  AS outflow_usd,
  ROUND(AVG(t.amount_usd), 2)                                       AS avg_txn_usd,
  ROUND(100 * AVG(CASE WHEN c.kyc_risk_rating = 'HIGH' THEN 1 ELSE 0 END), 2) AS high_risk_txn_pct
FROM ${silver.transactions} t
JOIN ${silver.customers} c
  ON  c.customer_id = t.customer_id
  AND t.txn_date >= c.effective_from
  AND (c.effective_to IS NULL OR t.txn_date <= c.effective_to)
GROUP BY DATE_TRUNC('MONTH', t.txn_date), c.segment
