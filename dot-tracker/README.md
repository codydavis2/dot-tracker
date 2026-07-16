# DOT Fleet & Maintenance Tracker — CS50 Final Project Scaffold

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Build the database
sqlite3 dot_tracker.db < schema.sql

# Run
flask run
```

## What's here

- `schema.sql` — full DB schema: users, vehicles, dot_details, maintenance_logs,
  dtc_codes, dtc_reference (seeded with common codes), inspection_reminders
- `app.py` — working routes for register/login/logout, vehicle CRUD (create/list/detail),
  maintenance logging, DTC logging, and DOT inspection reminders with overdue detection
- `helpers.py` — `login_required` decorator, `reminder_status()` (buckets a due date into
  overdue/due_soon/upcoming), `usd()` filter
- `templates/` — Bootstrap-based pages: login, register, dashboard, vehicle list, vehicle detail

## What's NOT done yet (on purpose — this is a scaffold, not a finished project)

- Editing/deleting vehicles, maintenance logs, DTC codes (currently create + read only)
- A form to fill in `dot_details` (DOT #, CDL class, last inspection) after creating a
  DOT-flagged vehicle — right now the row is created empty
- Input validation beyond the basics (e.g. VIN format, year ranges)
- The DOT inspection checklist mentioned in the scope (Tier 2 stretch) — not started
- Tests
- Styling polish

## Design notes for when you pick this back up

- `is_dot_regulated` on `vehicles` gates whether `dot_details` and DOT-type reminders
  are shown at all — this is your main branch point in templates and routes
- Dashboard queries all *incomplete* reminders across all the user's vehicles, sorted by
  due date, then colors them red/yellow/green in Python via `reminder_status()` rather
  than in Jinja — keeps that logic testable and in one place
- Every vehicle-scoped route re-checks `WHERE user_id = ?` so one user can't view or
  modify another user's fleet by guessing IDs in the URL
