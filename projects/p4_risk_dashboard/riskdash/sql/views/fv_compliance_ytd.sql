-- Zero-tolerance compliance events (RAS-004 s.5) counted year to date for each reporting day.
SELECT
  c.d                                                                   AS obs_date,
  COUNT(CASE WHEN e.event_type = 'SANCTIONS_BREACH' THEN 1 END)         AS sanctions_breaches,
  COUNT(CASE WHEN e.event_type = 'LATE_SAR' THEN 1 END)                 AS late_sars
FROM ${mart.calendar} c
LEFT JOIN ${mart.compliance_events} e
  ON  e.event_date <= c.d
  AND YEAR(e.event_date) = YEAR(c.d)
GROUP BY c.d
