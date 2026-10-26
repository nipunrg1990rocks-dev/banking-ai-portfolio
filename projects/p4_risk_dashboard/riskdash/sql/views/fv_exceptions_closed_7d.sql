-- Exceptions closed in the trailing 7 days of each reporting day, and whether each met its SLA.
SELECT
  c.d                                                   AS obs_date,
  e.exception_id,
  e.category,
  e.team,
  e.priority,
  e.regulated,
  e.opened_ts,
  e.due_ts,
  e.resolved_ts,
  e.sla_hours,
  CASE WHEN e.resolved_ts > e.due_ts THEN 1 ELSE 0 END  AS sla_missed,
  1                                                     AS closed_items
FROM ${mart.calendar} c
JOIN ${mart.exceptions} e
  ON CAST(e.resolved_ts AS DATE) BETWEEN DATE_SUB(c.d, 6) AND c.d
