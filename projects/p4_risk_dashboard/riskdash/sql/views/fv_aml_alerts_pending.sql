-- AML alert candidates from the Project 02 Gold layer that are pending review on each day.
-- AML-002 s.3: a Level 2 investigation must be completed within 30 calendar days of the alert.
SELECT
  c.d                                                               AS obs_date,
  a.alert_id,
  a.rule_code,
  a.customer_id,
  a.segment,
  a.kyc_risk_rating,
  a.priority,
  a.alert_date,
  a.amount_usd,
  DATEDIFF(c.d, a.alert_date)                                       AS age_days,
  CASE WHEN DATEDIFF(c.d, a.alert_date) > 30 THEN 1 ELSE 0 END      AS over_30_days,
  1                                                                 AS alerts
FROM ${mart.calendar} c
JOIN ${gold.aml_alert_candidates} a
  ON  a.alert_date <= c.d
  AND a.status = 'PENDING_REVIEW'
