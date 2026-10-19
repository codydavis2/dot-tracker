-- M3 — inspection types, red-item alerts.

-- Every inspection report has a type. The server picks the checklist from it
-- (app/constants.py) and decides who may submit it (app/permissions.py).
-- Reports filed before M3 had no type: they become 'general'. No default
-- afterward — every new report must say what kind of inspection it is.
ALTER TABLE inspection_reports ADD COLUMN inspection_type TEXT;
UPDATE inspection_reports SET inspection_type = 'general';
ALTER TABLE inspection_reports ALTER COLUMN inspection_type SET NOT NULL;
ALTER TABLE inspection_reports ADD CONSTRAINT inspection_reports_inspection_type_check
    CHECK (inspection_type IN ('pre_trip', 'monthly', 'annual', 'general'));

-- "Mark reviewed" on a red-item alert: only WHEN, deliberately not who or why.
ALTER TABLE inspection_reports ADD COLUMN reviewed_at TIMESTAMPTZ;

-- A work order created from an inspection points back at it. Optional; the
-- composite FK keeps the link inside one company (NULL is not checked).
ALTER TABLE work_orders ADD COLUMN inspection_report_id BIGINT;
ALTER TABLE work_orders ADD CONSTRAINT work_orders_inspection_report_fkey
    FOREIGN KEY (inspection_report_id, company_id) REFERENCES inspection_reports (id, company_id);
CREATE INDEX work_orders_inspection_report_id_idx ON work_orders (inspection_report_id);
