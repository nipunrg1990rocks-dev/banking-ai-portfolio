-- Daily volumes by transaction type and channel, in USD.
-- Grain: one row per txn_date x txn_type x channel x direction.
SELECT
  txn_date,
  txn_type,
  channel,
  direction,
  COUNT(*)                          AS txn_count,
  COUNT(DISTINCT customer_id)       AS active_customers,
  ROUND(SUM(amount_usd), 2)         AS total_amount_usd,
  ROUND(AVG(amount_usd), 2)         AS avg_amount_usd,
  ROUND(MAX(amount_usd), 2)         AS max_amount_usd
FROM ${silver.transactions}
GROUP BY txn_date, txn_type, channel, direction
