# DOT Fleet Tracker

A full-stack fleet maintenance and DOT compliance platform built with Flask and PostgreSQL. It gives a small trucking or service fleet one place to track vehicles, run digital inspections, manage work orders with parts and file attachments, and keep an eye on parts inventory — with a calendar dashboard that surfaces everything due or overdue at a glance.

This started as a CS50 final project scaffold (auth, a `vehicles` table, and a couple of CRUD routes) and grew into a ~1,200-line application spanning eight interlocking features. I'm documenting it the way I'd want a reviewer to read it: what it does, how it's built, and — more importantly — the specific problems I ran into and the reasoning behind how I fixed them.

## What it does

**Fleet management** — Vehicles are tracked by fleet unit number (not just year/make/model), with VIN, mileage, GVWR, and a DOT-regulated flag that gates a separate `dot_details` record (DOT #, CDL class, last roadside result).

**Digital inspections** — A configurable, flat inspection checklist (engine, suspension, brakes, electrical, etc.) filled out per vehicle. Each item gets a three-state color rating (green/yellow/red) via Bootstrap toggle buttons plus a free-text comment. If any item comes back red, the app prompts the inspector to spin up a work order pre-filled with the flagged items and comments — closing the loop between "found a problem" and "assigned it to get fixed" without re-typing anything.

**Work orders** — Full lifecycle: open → closed, with reassignment between vehicles, labor hours, a parts list, and file attachments (photos, invoices, quotes) stored and served through an authenticated download route rather than as static files. Every work order has its own detail page; edits, closes, and deletes redirect back to wherever the action was taken from instead of bouncing you to a fixed page.

**Recurring inspection reminders** — Reminders can repeat weekly, monthly, quarterly, or on a custom N-day/week/month interval, auto-generating up to two years of future occurrences at creation time.

**Parts inventory** — Part number, cost, vendor, storage location, quantity on hand, and a low-stock threshold that highlights anything running low, with inline search and inline editing.

**Calendar dashboard** — A month-navigable calendar (built on Python's `calendar` module, not a JS library) plotting every inspection and work order due that month, several to a day, color-coded by urgency, alongside a list of open work orders with overdue ones flagged in red.

It's multi-tenant: every table that holds fleet data carries a `company_id`, every query is scoped to the logged-in user's company, and the database itself refuses cross-company links (composite foreign keys). A user of one company gets a 404 on another company's vehicles, work orders, attachments, reports, or inventory, even by guessing an ID in the URL — verified by an automated two-company test across every view, edit, delete, and download route.

## Designing for two different users

The dashboard and the task screens are deliberately built for two different people with two different needs. The dashboard is the admin's view: a month-navigable calendar, an open-work-order list with overdue items called out, and a fleet compliance warning banner — everything a company admin needs to see fleet-wide status at a glance, without digging into any one vehicle. The individual task forms — filling out an inspection, adding a part to a work order, updating an inventory count — are the mechanic's view, and I optimized those for speed over completeness: forms stay hidden behind a button until you actually need them, statuses are one-click color toggles instead of dropdowns, related fields auto-populate instead of requiring re-entry, and nothing requires leaving the page you're on. A tech in a bay filling out a pre-trip inspection between jobs and an admin reconciling the week's compliance status from an office are doing fundamentally different work, and I tried to design each screen around the one it's actually for rather than making every page serve both audiences equally badly.

## Stack

Flask · PostgreSQL (raw SQL via `psycopg`, no ORM — I wanted to see every query) · Jinja2 · Bootstrap 5 · vanilla JS for the interactive bits (expanding parts lists, toggle-able forms, the color-coded checklist) — no frontend framework, no build step.

## Problems I hit, and how I reasoned through them

I think this section is more useful than a feature list — anyone can list features. These are the specific things that broke, why, and what fixing them properly (not just making the symptom go away) looked like.

### SQLite won't let you `ALTER` a `CHECK` constraint

Several times I needed to add new allowed values to a `CHECK` constraint — new reminder types, new work order statuses — after the table already existed. SQLite has no `ALTER TABLE ... MODIFY CONSTRAINT`. The correct fix is to rebuild the table: create a new one with the updated constraint, copy the data across, drop the old one, rename. I wrote that migration by hand each time rather than reaching for a migrations library, specifically checking row counts first so I never ran a destructive rebuild against data I couldn't reconstruct. Simple additive changes (a new nullable column) used `ALTER TABLE ADD COLUMN`, which SQLite does support — I treated "does this need a full rebuild or just an ADD COLUMN" as a real design question each time, not a reflex.

### Recurring date math had a silent, permanent drift bug

The first version of "repeat monthly" computed each occurrence by adding a month to the *previous* occurrence. That's wrong: Jan 31 + 1 month clamps to Feb 28, and if you then add another month to Feb 28 you get Mar 28 — not Mar 31. The series permanently loses the "31st" and drifts to the 28th forever. I caught this by generating a real series and eyeballing the output rather than trusting the logic on paper. The fix was to compute every occurrence from the *original* start date (`start + n intervals`) instead of chaining off the last one, so a 31st-of-the-month reminder correctly lands on the 31st in every month that has one and only clamps where it has to.

### A Jinja/Python naming collision that failed silently in a confusing way

When I grouped the inspection checklist into sections, each section was a dict: `{"section": "Engine", "items": [...]}`. Rendering `{% for item in section.items %}` blew up with `'builtin_function_or_method' object is not iterable`. The cause: Jinja resolves `section.items` via `getattr` first, and dicts already have a built-in `.items()` method — so it silently grabbed the *method*, not my key, and Python only complained once the template tried to iterate over it. I renamed the key to `fields` and moved on, but I flag it here because it's the kind of bug that's invisible until it isn't: it will bite anyone who names a dict key `items`, `keys`, `values`, or `get` and then touches it from a template.

### The dev server would quietly stop being useful mid-session

More than once, `flask run` had been restarted (by a reload, a crashed process, whatever) without the `--debug --host=0.0.0.0` flags I'd started it with. Two failure modes stacked on top of each other: without `--debug`, Jinja's `TEMPLATES_AUTO_RELOAD` defaults to off, so template edits stopped showing up even after a hard refresh — while the server kept responding normally, so nothing *looked* broken. And binding to `127.0.0.1` instead of `0.0.0.0` silently breaks port forwarding in a container/Codespaces environment. I diagnosed both with `ss -ltnp` (what's actually listening, and on which interface) and `curl` against the port directly, rather than guessing from browser symptoms — "the page looks stale" and "the page won't load at all" have different root causes and I wanted to confirm which one I was looking at before changing anything.

### Redirect targets that stopped making sense as the app grew

Work orders started as a flat list where every action (edit, close, delete) redirected back to `/work-orders`. Once each work order got its own detail page, that stopped being good UX — closing a work order from its detail page would silently kick you back to the list. I went through every mutating route (`close`, `edit`, `delete`, attachment delete) and made the *successful* path redirect back to the detail page it came from, while keeping failure paths (not found, wrong owner) pointing at the list, since there's no valid detail page to return to in that case. It's a small thing, but it's the difference between an app that feels like a sequence of separate CRUD screens and one that behaves like the user expects.

### Non-destructive schema evolution against a database I was actively using

I was populating the dev database with real sample data (seeded vehicles, work orders, inventory) while continuing to add features on top of it. That meant every schema change from that point on had to be a migration I ran against the *live* file, not a fresh `sqlite3 dot_tracker.db < schema.sql`. Before every change I checked whether the affected table actually had rows in it (`SELECT COUNT(*)`), which determined whether a plain `ADD COLUMN` was safe or whether I needed the full rebuild-and-copy dance from a `CHECK` constraint change. I never once discarded data by taking the fast path when it wasn't safe to.

## Setup

All commands run from the repo root. You need Python 3 and Docker.

```bash
# 1. Python environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 2. Local settings — copy the example, then edit .env
#    (set SECRET_KEY and POSTGRES_PASSWORD; set FLASK_DEBUG=1 for local development)
cp .env.example .env

# 3. Start PostgreSQL 16 (runs in Docker, data kept in a named volume)
docker compose up -d
docker compose ps           # wait until it shows "healthy"

# 4. Create / update the database tables
flask migrate

# 5. Optional: load a demo company with sample data (prints the logins)
flask seed-demo
```

`.env` is gitignored. Every setting is explained in `.env.example`. By default the app connects to the docker-compose database using the `POSTGRES_*` values; set `DATABASE_URL` to use a different server. `UPLOAD_DIR` is resolved from the repo root, so it doesn't matter which folder you start the app from.

The database is created with the `POSTGRES_*` credentials the first time the container starts. Changing them in `.env` later has no effect until you wipe the volume with `docker compose down -v` (which deletes all data).

### Database commands

```bash
flask migrate               # apply any new files in migrations/ (safe to run any time)
flask seed-demo             # create "Demo Fleet Co" with users, vehicles, work orders, inspections, inventory
docker compose exec postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'   # SQL prompt
docker compose down         # stop Postgres, keep data
```

**Schema changes** go in a new numbered file, e.g. `migrations/002_add_something.sql`, then run `flask migrate`. Each file runs once, in order, inside a transaction, and is recorded in the `schema_migrations` table. Never edit a migration that has already been committed.

`flask seed-demo` refuses to run if "Demo Fleet Co" already exists. The owner, mechanic and driver passwords are random and printed once. To start over, run `docker compose down -v`, then `docker compose up -d` and `flask migrate`.

## Running the app

```bash
flask run --host=0.0.0.0
```

Flask finds the `create_app()` factory in the `app/` package on its own. Debug mode (auto-reload, in-browser tracebacks) is **off unless `FLASK_DEBUG=1`** is set in `.env`. `--host=0.0.0.0` is needed for port forwarding in Codespaces/containers.

Sign up at `/register`. Signing up creates a company and its owner account; you log in with email and password.

## Running the tests

```bash
docker compose up -d        # tests need the Postgres container running
pytest
```

Tests use a separate database, `TEST_DATABASE_URL` (by default `<POSTGRES_DB>_test` on the same server, created automatically). **Everything in it is wiped**: the schema is rebuilt from the migrations at the start of each run and every table is emptied between tests. Your app database and `uploads/` are never touched. If Postgres isn't reachable, pytest stops and tells you to run `docker compose up -d`.

Most tests use two companies, A and B, each with an owner and a mechanic. The tenant-isolation tests (`tests/test_isolation.py`) check that a company-B user gets a 404 on every company-A resource. Tests marked `xfail` are known bugs recorded on purpose: each one's `reason` explains the bug, and they flip to failing (strict) once the bug is fixed, as a reminder to remove the marker.

## Project layout

- `app/` — the Flask application package
  - `__init__.py` — `create_app()` factory: config, sessions, CLI commands, blueprints
  - `config.py` — settings from the environment / `.env` (database URLs, upload folder)
  - `db.py` — `get_db()`: one Postgres connection per request (on `flask.g`), rows as dicts, closed (and rolled back on error) when the request ends
  - `migrate.py` / `seed.py` — the `flask migrate` and `flask seed-demo` commands
  - `auth.py` — signup, email login, and loading the logged-in user into `g.user` on every request
  - `dashboard.py`, `vehicles.py` (incl. maintenance logs + DTCs), `inspections.py` (reports + reminders), `work_orders.py` (incl. attachments), `inventory.py`, `audit.py` — one blueprint per feature. Every query is scoped to the logged-in user's `company_id`; another company's record is a 404
  - `people.py` — the shared "people dropdown" helpers: which users to list, and server-side validation of the submitted user
  - `constants.py` — the inspection checklist and status labels
  - `helpers.py` — `login_required`, `reminder_status()` (buckets a due date into overdue/due_soon/upcoming), the recurring-date-generation logic, and the `usd`/`date`/`datetime` Jinja filters
  - `templates/` — Bootstrap 5 pages; interactive bits (expanding parts lists, toggle-able "new item" forms, the inspection checklist) are plain vanilla JS, no framework
  - `static/` — stylesheet
- `migrations/` — numbered SQL migrations; `001_initial.sql` is the full multi-tenant schema
- `tests/` — pytest suite (see "Running the tests")
- `docker-compose.yml` — local PostgreSQL 16 container
- `.env.example` — every setting, with an explanation of each

## What I'd build next

- Automated tests (everything above was verified by hand with the Flask test client during development — formalizing that into a real `pytest` suite is the next thing I'd reach for)
- Role-based access (the `users.role` column already exists — `owner`/`mechanic`/`driver` — but nothing branches on it yet)
- The original `maintenance_logs` and `dtc_codes` tables and routes are still in the schema and backend but deliberately dropped from the UI once work orders and inspection reports became the primary way to record vehicle history — reviving or fully retiring them is an open decision, not an oversight
- Push/email notifications for overdue DOT inspections instead of relying on someone opening the dashboard
