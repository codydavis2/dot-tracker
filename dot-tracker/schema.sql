-- Vehicle Maintenance & DOT Compliance Tracker
-- SQLite schema

CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'owner' CHECK(role IN ('owner', 'mechanic', 'driver'))
);

CREATE TABLE vehicles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    unit_number TEXT,                  -- fleet-assigned identifier, e.g. "Unit 42"
    make TEXT NOT NULL,
    model TEXT NOT NULL,
    year INTEGER NOT NULL,
    vin TEXT UNIQUE,
    mileage INTEGER NOT NULL DEFAULT 0,
    is_dot_regulated BOOLEAN NOT NULL DEFAULT 0,
    gvwr INTEGER,                      -- gross vehicle weight rating (lbs); >=26001 typically triggers DOT/CDL rules
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

-- Only relevant when vehicles.is_dot_regulated = 1
CREATE TABLE dot_details (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id INTEGER NOT NULL UNIQUE,
    dot_number TEXT,
    cdl_class_required TEXT CHECK(cdl_class_required IN ('A', 'B', 'C', NULL)),
    last_annual_inspection_date DATE,
    next_annual_inspection_due DATE,
    last_roadside_result TEXT CHECK(last_roadside_result IN ('pass', 'warning', 'red_tag', NULL)),
    FOREIGN KEY (vehicle_id) REFERENCES vehicles(id)
);

CREATE TABLE maintenance_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id INTEGER NOT NULL,
    log_date DATE NOT NULL DEFAULT CURRENT_DATE,
    service_type TEXT NOT NULL,        -- e.g. "oil change", "brake service"
    mileage_at_service INTEGER,
    cost NUMERIC,
    notes TEXT,
    mechanic_name TEXT,
    FOREIGN KEY (vehicle_id) REFERENCES vehicles(id)
);

CREATE TABLE maintenance_parts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    maintenance_log_id INTEGER NOT NULL,
    part_name TEXT NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (maintenance_log_id) REFERENCES maintenance_logs(id)
);

CREATE TABLE dtc_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id INTEGER NOT NULL,
    code TEXT NOT NULL,                -- e.g. "P0420"
    description TEXT,
    date_logged DATE NOT NULL DEFAULT CURRENT_DATE,
    resolved BOOLEAN NOT NULL DEFAULT 0,
    FOREIGN KEY (vehicle_id) REFERENCES vehicles(id)
);

-- Reference table so DTC descriptions aren't hand-typed every time
CREATE TABLE dtc_reference (
    code TEXT PRIMARY KEY,
    description TEXT NOT NULL
);

CREATE TABLE work_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open', 'closed')),
    created_by TEXT,
    assigned_to TEXT,                  -- mechanic
    description TEXT,
    notes TEXT,
    hours NUMERIC,                     -- labor hours
    scheduled_completion_date DATE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    closed_at TIMESTAMP,
    FOREIGN KEY (vehicle_id) REFERENCES vehicles(id)
);

CREATE TABLE work_order_parts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    work_order_id INTEGER NOT NULL,
    part_name TEXT NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (work_order_id) REFERENCES work_orders(id)
);

CREATE TABLE work_order_attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    work_order_id INTEGER NOT NULL,
    original_filename TEXT NOT NULL,
    stored_filename TEXT NOT NULL,     -- randomized name on disk; avoids collisions/traversal
    content_type TEXT,
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (work_order_id) REFERENCES work_orders(id)
);

CREATE TABLE inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    part_number TEXT,
    name TEXT NOT NULL,
    cost NUMERIC,
    vendor TEXT,
    location TEXT,
    quantity INTEGER NOT NULL DEFAULT 0,
    low_stock_threshold INTEGER,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE inspection_reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id INTEGER NOT NULL,
    reminder_type TEXT NOT NULL CHECK(
        reminder_type IN (
            'dot_annual', 'pre_trip',
            'weekly', 'monthly', 'quarterly', 'other'
        )
    ),
    due_date DATE NOT NULL,
    status TEXT NOT NULL DEFAULT 'upcoming' CHECK(
        status IN ('upcoming', 'overdue', 'completed')
    ),
    mechanic_name TEXT,
    FOREIGN KEY (vehicle_id) REFERENCES vehicles(id)
);

CREATE INDEX idx_vehicles_user ON vehicles(user_id);
CREATE UNIQUE INDEX idx_vehicles_unit_number ON vehicles(user_id, unit_number);
CREATE INDEX idx_maintenance_vehicle ON maintenance_logs(vehicle_id);
CREATE INDEX idx_dtc_vehicle ON dtc_codes(vehicle_id);
CREATE INDEX idx_reminders_vehicle ON inspection_reminders(vehicle_id);
CREATE INDEX idx_reminders_due ON inspection_reminders(due_date);
CREATE INDEX idx_parts_log ON maintenance_parts(maintenance_log_id);
CREATE INDEX idx_work_orders_vehicle ON work_orders(vehicle_id);
CREATE INDEX idx_work_orders_status ON work_orders(status);
CREATE INDEX idx_work_order_parts_wo ON work_order_parts(work_order_id);
CREATE INDEX idx_work_order_attachments_wo ON work_order_attachments(work_order_id);
CREATE INDEX idx_inventory_user ON inventory(user_id);

-- Seed a handful of common DTC codes to start (expand as needed)
INSERT INTO dtc_reference (code, description) VALUES
    ('P0300', 'Random/multiple cylinder misfire detected'),
    ('P0420', 'Catalyst system efficiency below threshold (Bank 1)'),
    ('P0171', 'System too lean (Bank 1)'),
    ('P0442', 'EVAP system leak detected (small leak)'),
    ('P0128', 'Coolant thermostat below regulating temperature'),
    ('P0505', 'Idle control system malfunction'),
    ('P0700', 'Transmission control system malfunction');
