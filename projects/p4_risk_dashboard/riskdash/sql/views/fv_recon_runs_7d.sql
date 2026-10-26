-- Reconciliation match results for the trailing 7 days of each reporting day.
SELECT
  c.d               AS obs_date,
  r.run_date,
  r.recon_id,
  x.owner_team,
  r.items_total,
  r.auto_matched,
  r.manual_matched,
  r.breaks_raised
FROM ${mart.calendar} c
JOIN ${mart.recon_runs} r
  ON r.run_date BETWEEN DATE_SUB(c.d, 6) AND c.d
JOIN ${mart.recons} x
  ON x.recon_id = r.recon_id
