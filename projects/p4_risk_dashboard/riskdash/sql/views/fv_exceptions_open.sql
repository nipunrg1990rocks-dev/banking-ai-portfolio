-- Operational exceptions still open at the end of each reporting day (23:59:59).
WITH days AS (
  SELECT d, CAST(DATE_ADD(d, 1) AS TIMESTAMP) AS day_end FROM ${mart.calendar}
)
SELECT
  c.d                                                         AS obs_date,
  e.exception_id,
  e.category,
  e.team,
  e.priority,
  e.regulated,
  e.opened_ts,
  e.due_ts,
  e.amount_usd,
  DATEDIFF(c.d, CAST(e.opened_ts AS DATE))                    AS age_days,
  CASE
    WHEN DATEDIFF(c.d, CAST(e.opened_ts AS DATE)) < 1  THEN 'under 1 day'
    WHEN DATEDIFF(c.d, CAST(e.opened_ts AS DATE)) <= 2 THEN '1-2 days'
    WHEN DATEDIFF(c.d, CAST(e.opened_ts AS DATE)) <= 5 THEN '3-5 days'
    ELSE 'over 5 days'
  END                                                         AS age_bucket,
  CASE WHEN e.due_ts < c.day_end THEN 1 ELSE 0 END            AS past_due,
  1                                                           AS open_items
FROM days c
JOIN ${mart.exceptions} e
  ON  e.opened_ts < c.day_end
  AND (e.resolved_ts IS NULL OR e.resolved_ts >= c.day_end)
