-- RAS-004 s.5: overdue High risk KYC periodic reviews as a share of the High risk population.
-- Population: current HIGH risk customers in the Project 02 Gold customer_360 table.
-- A review is overdue from the day after its due date until it is completed.
WITH high_risk AS (
  SELECT customer_id, segment, region
  FROM ${gold.customer_360}
  WHERE kyc_risk_rating = 'HIGH'
)
SELECT
  c.d                                                                     AS obs_date,
  h.customer_id,
  h.segment,
  h.region,
  MIN(CASE WHEN k.due_date < c.d AND (k.completed_date IS NULL OR k.completed_date > c.d)
           THEN k.due_date END)                                           AS overdue_since,
  MAX(CASE WHEN k.due_date < c.d AND (k.completed_date IS NULL OR k.completed_date > c.d)
           THEN 1 ELSE 0 END)                                             AS overdue,
  1                                                                       AS customers
FROM ${mart.calendar} c
CROSS JOIN high_risk h
LEFT JOIN ${mart.kyc_reviews} k
  ON k.customer_id = h.customer_id
GROUP BY c.d, h.customer_id, h.segment, h.region
