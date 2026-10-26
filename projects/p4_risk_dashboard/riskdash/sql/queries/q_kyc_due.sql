-- High-risk KYC reviews not completed by the reporting day: overdue now, or falling due soon.
SELECT k.review_id, k.customer_id, h.segment, h.region, k.due_date, k.completed_date
FROM ${mart.kyc_reviews} k
JOIN ${gold.customer_360} h ON h.customer_id = k.customer_id AND h.kyc_risk_rating = 'HIGH'
WHERE k.due_date <= DATE_ADD(${as_of}, 90)
  AND (k.completed_date IS NULL OR k.completed_date > ${as_of})
ORDER BY k.due_date, k.customer_id
