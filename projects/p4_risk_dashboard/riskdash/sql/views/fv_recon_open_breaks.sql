-- Reconciliation breaks still open at the end of each reporting day, with their age.
SELECT
  c.d                                                       AS obs_date,
  b.break_id,
  b.recon_id,
  b.break_type,
  b.owner_team,
  b.root_cause,
  b.opened_date,
  b.amount_usd,
  DATEDIFF(c.d, b.opened_date)                              AS age_days,
  CASE
    WHEN DATEDIFF(c.d, b.opened_date) <= 2  THEN '0-2 days'
    WHEN DATEDIFF(c.d, b.opened_date) <= 5  THEN '3-5 days'
    WHEN DATEDIFF(c.d, b.opened_date) <= 10 THEN '6-10 days'
    ELSE 'over 10 days'
  END                                                       AS age_bucket,
  CASE WHEN DATEDIFF(c.d, b.opened_date) > 5 THEN 1 ELSE 0 END AS aged_over_5d,
  1                                                         AS breaks
FROM ${mart.calendar} c
JOIN ${mart.recon_breaks} b
  ON  b.opened_date <= c.d
  AND (b.resolved_date IS NULL OR b.resolved_date > c.d)
