# Risk Insights Agent back-test

Replayed 50 business days (2026-08-03 to 2026-10-09); on each day the agent saw only the data up to that day.

- Incidents detected with the right cause: **100%** (7 of 7)
- Median detection lag: **0 business day(s)** after onset
- Wrong root causes on any day: **0**
- Average findings per briefing: 3.0

| Incident | Metric | Cause | Onset | First detected | Lag (business days) |
|---|---|---|---|---|---|
| INC-1 | RECON_OPEN_BREAKS | CHG-2026-1043 | 2026-10-05 | 2026-10-05 | 0 |
| INC-2 | DQ_HEALTH_SCORE | INC-2026-0871 | 2026-10-07 | 2026-10-07 | 0 |
| INC-3 | DQ_RULES_BREACHED | CHG-2026-0955 | 2026-08-17 | 2026-08-19 | 2 |
| INC-4 | EXC_SLA_BREACH_PCT | BUS-2026-0012 | 2026-09-28 | 2026-09-30 | 2 |
| INC-5 | KRI_HR_KYC_OVERDUE_PCT | (no change: overdue item) | 2026-09-26 | 2026-09-28 | 0 |
| INC-6 | KRI_LCR_PCT | BUS-2026-0019 | 2026-10-06 | 2026-10-07 | 1 |
| INC-7 | KRI_OPLOSS_YTD_USD_M | INC-2026-0815 | 2026-09-22 | 2026-09-22 | 0 |
