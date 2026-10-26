-- Operational-loss events from 1 January of the reporting year up to each reporting day.
SELECT
  c.d                  AS obs_date,
  l.event_id,
  l.event_date,
  l.basel_event_type,
  l.business_line,
  l.gross_loss_usd,
  l.recovery_usd,
  l.net_loss_usd,
  l.description
FROM ${mart.calendar} c
JOIN ${mart.op_loss_events} l
  ON  l.event_date <= c.d
  AND YEAR(l.event_date) = YEAR(c.d)
