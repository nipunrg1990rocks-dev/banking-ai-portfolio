-- Rule-based AML alert candidates for analyst review. These are NOT decisions:
-- every row starts as PENDING_REVIEW and needs a human investigator.
-- Thresholds are illustrative for a synthetic bank, not regulatory guidance.
WITH txn AS (
  SELECT * FROM ${silver.transactions}
),
structuring AS (
  -- Three or more cash deposits just under 10,000 USD on the same day.
  SELECT
    customer_id,
    txn_date                          AS alert_date,
    'STRUCTURING'                     AS rule_code,
    COUNT(*)                          AS txn_count,
    ROUND(SUM(amount_usd), 2)         AS amount_usd,
    CONCAT(COUNT(*), ' cash deposits between 9,000 and 9,999.99 USD on ', txn_date,
           ', total ', FORMAT_NUMBER(SUM(amount_usd), 2), ' USD') AS rationale,
    ARRAY_SORT(COLLECT_LIST(txn_id))  AS txn_ids
  FROM txn
  WHERE txn_type = 'CASH_DEPOSIT' AND amount_usd BETWEEN 9000 AND 9999.99
  GROUP BY customer_id, txn_date
  HAVING COUNT(*) >= 3
),
high_risk_jurisdiction AS (
  -- Any wire of 10,000 USD or more to or from a HIGH risk jurisdiction.
  SELECT
    customer_id,
    txn_date                          AS alert_date,
    'HIGH_RISK_JURISDICTION'          AS rule_code,
    1                                 AS txn_count,
    amount_usd,
    CONCAT(txn_type, ' of ', FORMAT_NUMBER(amount_usd, 2), ' USD with counterparty in ',
           counterparty_country, ' (risk tier HIGH)') AS rationale,
    ARRAY(txn_id)                     AS txn_ids
  FROM txn
  WHERE txn_type IN ('WIRE_IN', 'WIRE_OUT')
    AND counterparty_risk_tier = 'HIGH'
    AND amount_usd >= 10000
),
rapid_movement AS (
  -- A wire in of 50,000 USD or more, with 90% or more wired out again within 48 hours.
  SELECT
    i.customer_id,
    i.txn_date                        AS alert_date,
    'RAPID_MOVEMENT'                  AS rule_code,
    1 + COUNT(o.txn_id)               AS txn_count,
    ROUND(i.amount_usd, 2)            AS amount_usd,
    CONCAT('Wire in of ', FORMAT_NUMBER(i.amount_usd, 2), ' USD, ',
           FORMAT_NUMBER(SUM(o.amount_usd), 2), ' USD wired out within 48 hours') AS rationale,
    ARRAY_SORT(CONCAT(ARRAY(i.txn_id), COLLECT_LIST(o.txn_id))) AS txn_ids
  FROM txn i
  JOIN txn o
    ON  o.customer_id = i.customer_id
    AND o.txn_type = 'WIRE_OUT'
    AND o.txn_ts BETWEEN i.txn_ts AND i.txn_ts + INTERVAL 48 HOURS
  WHERE i.txn_type = 'WIRE_IN' AND i.amount_usd >= 50000
  GROUP BY i.customer_id, i.txn_date, i.amount_usd, i.txn_id
  HAVING SUM(o.amount_usd) >= 0.9 * i.amount_usd
),
alerts AS (
  SELECT * FROM structuring
  UNION ALL SELECT * FROM high_risk_jurisdiction
  UNION ALL SELECT * FROM rapid_movement
)
SELECT
  SHA2(CONCAT_WS('|', a.rule_code, a.customer_id, a.alert_date, a.txn_ids[0]), 256) AS alert_id,
  a.rule_code,
  a.customer_id,
  c.segment,
  c.kyc_risk_rating,
  c.pep_flag,
  a.alert_date,
  a.txn_count,
  a.amount_usd,
  a.rationale,
  a.txn_ids,
  CASE
    WHEN a.rule_code IN ('HIGH_RISK_JURISDICTION', 'RAPID_MOVEMENT')
      OR c.kyc_risk_rating = 'HIGH' OR c.pep_flag THEN 'HIGH'
    ELSE 'MEDIUM'
  END                                 AS priority,
  'PENDING_REVIEW'                    AS status
FROM alerts a
LEFT JOIN ${silver.customers} c
  ON c.customer_id = a.customer_id AND c.is_current
