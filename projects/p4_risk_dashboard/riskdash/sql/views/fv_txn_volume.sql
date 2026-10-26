-- Daily payment volumes from the Project 02 Gold layer (USD).
SELECT
  txn_date            AS obs_date,
  txn_type,
  channel,
  direction,
  txn_count,
  total_amount_usd
FROM ${gold.daily_transaction_summary}
