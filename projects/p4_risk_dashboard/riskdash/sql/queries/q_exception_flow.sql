-- Daily exception arrivals and closures per team (used to explain backlog moves).
WITH opened AS (
  SELECT team, CAST(opened_ts AS DATE) AS d, COUNT(*) AS opened
  FROM ${mart.exceptions} GROUP BY team, CAST(opened_ts AS DATE)
),
closed AS (
  SELECT team, CAST(resolved_ts AS DATE) AS d, COUNT(*) AS closed
  FROM ${mart.exceptions} WHERE resolved_ts IS NOT NULL GROUP BY team, CAST(resolved_ts AS DATE)
)
SELECT c.d AS obs_date, t.team, COALESCE(o.opened, 0) AS opened, COALESCE(x.closed, 0) AS closed
FROM ${mart.calendar} c
CROSS JOIN (SELECT DISTINCT team FROM ${mart.exceptions}) t
LEFT JOIN opened o ON o.team = t.team AND o.d = c.d
LEFT JOIN closed x ON x.team = t.team AND x.d = c.d
ORDER BY t.team, c.d
