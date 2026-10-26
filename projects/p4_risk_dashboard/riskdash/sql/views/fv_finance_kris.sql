-- Capital, liquidity and credit KRIs reported by Finance and Treasury (one row per day).
SELECT
  obs_date,
  MAX(CASE WHEN kri_id = 'CET1_RATIO' THEN value END) AS cet1_pct,
  MAX(CASE WHEN kri_id = 'LCR'        THEN value END) AS lcr_pct,
  MAX(CASE WHEN kri_id = 'NPL_RATIO'  THEN value END) AS npl_pct
FROM ${mart.finance_kris}
GROUP BY obs_date
