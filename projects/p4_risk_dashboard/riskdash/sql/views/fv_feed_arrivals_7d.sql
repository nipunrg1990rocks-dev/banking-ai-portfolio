-- For each reporting day, every source-system feed delivered in the trailing 7 days.
SELECT
  c.d                                         AS obs_date,
  f.business_date,
  f.source_system,
  f.feed_name,
  f.sla_ts,
  f.arrived_ts,
  f.minutes_vs_sla,
  CASE WHEN f.on_time THEN 1 ELSE 0 END       AS on_time,
  1                                           AS feeds
FROM ${mart.calendar} c
JOIN ${mart.feed_arrivals} f
  ON f.business_date BETWEEN DATE_SUB(c.d, 6) AND c.d
