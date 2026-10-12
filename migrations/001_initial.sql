-- Gladhand — initial multi-tenant schema (PostgreSQL).
--
-- Conventions:
--   * Every customer-data table has company_id. The only table without one is
--     dtc_reference (global reference data shared by all companies).
--   * Tables that other tables point to have UNIQUE (id, company_id), and every child
--     foreign key is composite: (parent_id, company_id) REFERENCES parent (id, company_id).
--     This makes the database itself refuse a row that links to another company's data.
--     (A composite FK with a NULL column is not checked, so optional links still work.)
--   * TIMESTAMPTZ for moments in time, DATE for calendar dates, NUMERIC(10,2) for money.
--   * deleted_at / archived_at: NULL means live. Soft-deleted rows stay for the audit trail.

CREATE TABLE companies (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name         TEXT NOT NULL,
    company_type TEXT NOT NULL DEFAULT 'fleet' CHECK (company_type IN ('fleet', 'shop')),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE users (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    company_id    BIGINT NOT NULL REFERENCES companies (id),
    email         TEXT NOT NULL UNIQUE CHECK (email = lower(btrim(email))),
    password_hash TEXT NOT NULL,
    full_name     TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('owner', 'manager', 'mechanic', 'driver')),
    is_active     BOOLEAN NOT NULL DEFAULT true,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at TIMESTAMPTZ,
    UNIQUE (id, company_id)
);
-- Exactly one owner per company (ownership transfer = demote, then promote, in one transaction)
CREATE UNIQUE INDEX users_one_owner_per_company ON users (company_id) WHERE role = 'owner';

CREATE TABLE vehicles (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    company_id       BIGINT NOT NULL REFERENCES companies (id),
    unit_number      TEXT,              -- fleet-assigned identifier, e.g. "42"
    make             TEXT NOT NULL,
    model            TEXT NOT NULL,
    year             INTEGER NOT NULL,
    vin              TEXT,
    mileage          INTEGER NOT NULL DEFAULT 0,
    is_dot_regulated BOOLEAN NOT NULL DEFAULT false,
    gvwr             INTEGER,           -- gross vehicle weight rating (lbs)
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    archived_at      TIMESTAMPTZ,       -- NULL = active
    UNIQUE (id, company_id),
    CONSTRAINT vehicles_company_unit_number_key UNIQUE (company_id, unit_number),
    CONSTRAINT vehicles_company_vin_key UNIQUE (company_id, vin)
);

-- Only relevant when vehicles.is_dot_regulated. The USDOT number is per vehicle on
-- purpose: one company can run trucks under several DOT numbers.
CREATE TABLE dot_details (
    id                          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    company_id                  BIGINT NOT NULL REFERENCES companies (id),
    vehicle_id                  BIGINT NOT NULL UNIQUE,
    dot_number                  TEXT,
    cdl_class_required          TEXT CHECK (cdl_class_required IN ('A', 'B', 'C')),
    last_annual_inspection_date DATE,
    next_annual_inspection_due  DATE,
    last_roadside_result        TEXT CHECK (last_roadside_result IN ('pass', 'warning', 'red_tag')),
    FOREIGN KEY (vehicle_id, company_id) REFERENCES vehicles (id, company_id)
);

CREATE TABLE maintenance_logs (
    id                   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    company_id           BIGINT NOT NULL REFERENCES companies (id),
    vehicle_id           BIGINT NOT NULL,
    log_date             DATE NOT NULL DEFAULT CURRENT_DATE,
    service_type         TEXT NOT NULL,     -- e.g. "oil change", "brake service"
    mileage_at_service   INTEGER,
    cost                 NUMERIC(10, 2),
    notes                TEXT,
    performed_by_user_id BIGINT NOT NULL,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at           TIMESTAMPTZ,
    UNIQUE (id, company_id),
    FOREIGN KEY (vehicle_id, company_id) REFERENCES vehicles (id, company_id),
    FOREIGN KEY (performed_by_user_id, company_id) REFERENCES users (id, company_id)
);

CREATE TABLE maintenance_parts (
    id                 BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    company_id         BIGINT NOT NULL REFERENCES companies (id),
    maintenance_log_id BIGINT NOT NULL,
    part_name          TEXT NOT NULL,
    quantity           INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (maintenance_log_id, company_id) REFERENCES maintenance_logs (id, company_id)
);

CREATE TABLE dtc_codes (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    company_id  BIGINT NOT NULL REFERENCES companies (id),
    vehicle_id  BIGINT NOT NULL,
    code        TEXT NOT NULL,
    description TEXT,
    date_logged DATE NOT NULL DEFAULT CURRENT_DATE,
    resolved    BOOLEAN NOT NULL DEFAULT false,
    FOREIGN KEY (vehicle_id, company_id) REFERENCES vehicles (id, company_id)
);

-- Global reference data shared by every company — the one table with no company_id.
CREATE TABLE dtc_reference (
    code        TEXT PRIMARY KEY,
    description TEXT NOT NULL
);

CREATE TABLE work_orders (
    id                        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    company_id                BIGINT NOT NULL REFERENCES companies (id),
    vehicle_id                BIGINT NOT NULL,
    status                    TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    created_by_user_id        BIGINT NOT NULL,   -- "Created by"
    assigned_to_user_id       BIGINT,            -- "Assigned to"
    performed_by_user_id      BIGINT,            -- "Completed by"; required once closed
    description               TEXT,
    notes                     TEXT,
    hours                     NUMERIC(6, 2),     -- labor hours
    scheduled_completion_date DATE,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at                 TIMESTAMPTZ,
    deleted_at                TIMESTAMPTZ,
    UNIQUE (id, company_id),
    CONSTRAINT work_orders_closed_needs_completed_by
        CHECK (status <> 'closed' OR performed_by_user_id IS NOT NULL),
    FOREIGN KEY (vehicle_id, company_id) REFERENCES vehicles (id, company_id),
    FOREIGN KEY (created_by_user_id, company_id) REFERENCES users (id, company_id),
    FOREIGN KEY (assigned_to_user_id, company_id) REFERENCES users (id, company_id),
    FOREIGN KEY (performed_by_user_id, company_id) REFERENCES users (id, company_id)
);

CREATE TABLE work_order_parts (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    company_id    BIGINT NOT NULL REFERENCES companies (id),
    work_order_id BIGINT NOT NULL,
    part_name     TEXT NOT NULL,
    quantity      INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (work_order_id, company_id) REFERENCES work_orders (id, company_id)
);

CREATE TABLE work_order_attachments (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    company_id        BIGINT NOT NULL REFERENCES companies (id),
    work_order_id     BIGINT NOT NULL,
    original_filename TEXT NOT NULL,
    stored_filename   TEXT NOT NULL,     -- randomized name on disk; avoids collisions/traversal
    content_type      TEXT,
    uploaded_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (work_order_id, company_id) REFERENCES work_orders (id, company_id)
);

CREATE TABLE inventory (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    company_id          BIGINT NOT NULL REFERENCES companies (id),
    part_number         TEXT,
    name                TEXT NOT NULL,
    cost                NUMERIC(10, 2),
    vendor              TEXT,
    location            TEXT,
    quantity            INTEGER NOT NULL DEFAULT 0,
    low_stock_threshold INTEGER
);

CREATE TABLE inspection_reminders (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    company_id          BIGINT NOT NULL REFERENCES companies (id),
    vehicle_id          BIGINT NOT NULL,
    reminder_type       TEXT NOT NULL CHECK (
        reminder_type IN ('dot_annual', 'pre_trip', 'weekly', 'monthly', 'quarterly', 'other')
    ),
    due_date            DATE NOT NULL,
    status              TEXT NOT NULL DEFAULT 'upcoming' CHECK (status IN ('upcoming', 'overdue', 'completed')),
    assigned_to_user_id BIGINT,
    FOREIGN KEY (vehicle_id, company_id) REFERENCES vehicles (id, company_id),
    FOREIGN KEY (assigned_to_user_id, company_id) REFERENCES users (id, company_id)
);

-- A filled-out inspection checklist (distinct from inspection_reminders, which only
-- schedule when an inspection is due).
CREATE TABLE inspection_reports (
    id                   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    company_id           BIGINT NOT NULL REFERENCES companies (id),
    vehicle_id           BIGINT NOT NULL,
    performed_by_user_id BIGINT NOT NULL,    -- the inspector
    inspection_date      DATE NOT NULL DEFAULT CURRENT_DATE,
    mileage              INTEGER,
    notes                TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at           TIMESTAMPTZ,
    UNIQUE (id, company_id),
    FOREIGN KEY (vehicle_id, company_id) REFERENCES vehicles (id, company_id),
    FOREIGN KEY (performed_by_user_id, company_id) REFERENCES users (id, company_id)
);

-- One row per checklist item. Item text is stored directly (not a reference to a
-- checklist definition) so past reports stay accurate if the checklist changes.
CREATE TABLE inspection_report_items (
    id                   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    company_id           BIGINT NOT NULL REFERENCES companies (id),
    inspection_report_id BIGINT NOT NULL,
    item                 TEXT NOT NULL,
    status               TEXT NOT NULL DEFAULT 'good' CHECK (status IN ('good', 'needs_attention', 'out_of_spec')),
    comments             TEXT,
    FOREIGN KEY (inspection_report_id, company_id) REFERENCES inspection_reports (id, company_id)
);

-- Indexes: every company_id and every foreign key column.
CREATE INDEX users_company_id_idx                   ON users (company_id);
CREATE INDEX vehicles_company_id_idx                ON vehicles (company_id);
CREATE INDEX dot_details_company_id_idx             ON dot_details (company_id);
CREATE INDEX maintenance_logs_company_id_idx        ON maintenance_logs (company_id);
CREATE INDEX maintenance_logs_vehicle_id_idx        ON maintenance_logs (vehicle_id);
CREATE INDEX maintenance_logs_performed_by_idx      ON maintenance_logs (performed_by_user_id);
CREATE INDEX maintenance_parts_company_id_idx       ON maintenance_parts (company_id);
CREATE INDEX maintenance_parts_log_id_idx           ON maintenance_parts (maintenance_log_id);
CREATE INDEX dtc_codes_company_id_idx               ON dtc_codes (company_id);
CREATE INDEX dtc_codes_vehicle_id_idx               ON dtc_codes (vehicle_id);
CREATE INDEX work_orders_company_id_idx             ON work_orders (company_id);
CREATE INDEX work_orders_vehicle_id_idx             ON work_orders (vehicle_id);
CREATE INDEX work_orders_created_by_idx             ON work_orders (created_by_user_id);
CREATE INDEX work_orders_assigned_to_idx            ON work_orders (assigned_to_user_id);
CREATE INDEX work_orders_performed_by_idx           ON work_orders (performed_by_user_id);
CREATE INDEX work_order_parts_company_id_idx        ON work_order_parts (company_id);
CREATE INDEX work_order_parts_work_order_id_idx     ON work_order_parts (work_order_id);
CREATE INDEX work_order_attachments_company_id_idx  ON work_order_attachments (company_id);
CREATE INDEX work_order_attachments_wo_id_idx       ON work_order_attachments (work_order_id);
CREATE INDEX inventory_company_id_idx               ON inventory (company_id);
CREATE INDEX inspection_reminders_company_id_idx    ON inspection_reminders (company_id);
CREATE INDEX inspection_reminders_vehicle_id_idx    ON inspection_reminders (vehicle_id);
CREATE INDEX inspection_reminders_assigned_to_idx   ON inspection_reminders (assigned_to_user_id);
CREATE INDEX inspection_reminders_due_date_idx      ON inspection_reminders (due_date);
CREATE INDEX inspection_reports_company_id_idx      ON inspection_reports (company_id);
CREATE INDEX inspection_reports_vehicle_id_idx      ON inspection_reports (vehicle_id);
CREATE INDEX inspection_reports_performed_by_idx    ON inspection_reports (performed_by_user_id);
CREATE INDEX inspection_report_items_company_id_idx ON inspection_report_items (company_id);
CREATE INDEX inspection_report_items_report_id_idx  ON inspection_report_items (inspection_report_id);
-- dot_details.vehicle_id is already indexed by its UNIQUE constraint.

-- Seed rows carried over from the SQLite schema. NOTE: these are passenger-car OBD-II
-- P-codes; heavy-duty trucks report J1939 SPN/FMI codes. To be revisited.
INSERT INTO dtc_reference (code, description) VALUES
    ('P0300', 'Random/multiple cylinder misfire detected'),
    ('P0420', 'Catalyst system efficiency below threshold (Bank 1)'),
    ('P0171', 'System too lean (Bank 1)'),
    ('P0442', 'EVAP system leak detected (small leak)'),
    ('P0128', 'Coolant thermostat below regulating temperature'),
    ('P0505', 'Idle control system malfunction'),
    ('P0700', 'Transmission control system malfunction');
