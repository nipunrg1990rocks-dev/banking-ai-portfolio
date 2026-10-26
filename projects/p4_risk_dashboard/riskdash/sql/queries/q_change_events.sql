-- IT change, incident and business-change calendar.
SELECT change_id, event_date, system, change_type, description
FROM ${mart.change_events}
ORDER BY event_date
