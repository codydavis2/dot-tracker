# Gladhand — Project Rules for Claude Code

Read this file fully before starting any task. These rules override general habits.
If a task seems to conflict with a rule here, stop and ask instead of guessing.

## What this is

Gladhand (repo name: dot-tracker) is a multi-tenant SaaS for fleet maintenance and
DOT compliance. Customers are small trucking/service fleets (1–50 vehicles) that run
their own in-house shop. A future "shop mode" will serve repair shops that do
customer work — design data structures so that mode can be added later, but do not
build it unless a task asks for it.

Core features: vehicles, inspections (DVIR and annual 49 CFR 396.17), work orders,
parts inventory, file attachments, maintenance logs, recurring reminders, audit reports.

The builder is a former diesel mechanic. Domain accuracy matters as much as code
quality — when in doubt about trucking/DOT terminology or rules, flag it rather
than invent it.

## Stack

- Python / Flask, Jinja2 templates, Bootstrap 5, vanilla JS (no frontend framework, no build step)
- Database: **PostgreSQL** via `psycopg` (migrating from SQLite — see "Current state")
- **Raw SQL only. No ORM.** This is a deliberate choice; do not introduce SQLAlchemy or similar.
- File storage: local disk in development, Amazon S3 in production (planned)
- Email: Amazon SES (planned)
- Tests: pytest

## Current state (update this section as milestones complete)

- Flat repo: everything lives at the repo root. App code is the `app/` package with a
  `create_app()` factory and one blueprint per feature: `auth`, `dashboard`, `vehicles` (incl.
  maintenance logs + DTCs), `inspections` (reports + reminders), `work_orders` (incl.
  attachments), `inventory`, `audit`. URL paths are hard-coded in templates and redirects (no `url_for`).
- **PostgreSQL** (docker-compose, `docker compose up -d`) via psycopg 3. Schema lives in
  numbered SQL migrations in `migrations/` (`001_initial.sql` is the whole M1 schema), applied
  with `flask migrate`. There is no schema.sql and no SQLite anymore.
- **Multi-tenant** (M1 done): every customer-data table has `company_id`; every query is scoped
  to `g.user["company_id"]`. Signup (`/register`) creates a company and its owner together.
- **Email + password login.** The session stores only `user_id`; `auth.load_logged_in_user`
  reloads the user into `g.user` on every request and logs out missing/deactivated users.
- **Roles are stored but NOT enforced yet** (that's M3). Every signup is an owner; there is no
  invite flow or user-management UI yet (users other than the owner exist only via SQL / seed).
- Not done yet (required by rules below, planned for later milestones): CSRF protection, login
  rate limiting, invites, role enforcement, S3, SES.
- `flask seed-demo` creates "Demo Fleet Co" with sample users and data and prints the logins.
- Config comes from environment variables / `.env` (`SECRET_KEY`, `DATABASE_URL`,
  `TEST_DATABASE_URL`, `UPLOAD_DIR`, `FLASK_DEBUG`, `POSTGRES_*`); see `.env.example`.
- Tests: pytest against a separate Postgres database (`TEST_DATABASE_URL`, wiped on every run),
  with two-company fixtures (A and B, each with an owner and a mechanic). Known bugs are recorded
  as `xfail(strict=True)` tests with a reason; when fixing one, remove its marker.

## NON-NEGOTIABLE RULES

### 1. Tenant isolation
- Every table holding customer data has a `company_id` column.
- **Every query that reads or writes customer data MUST filter by `company_id`**, taken from
  the server-side session — never from a form field, URL parameter, or request body.
- Looking up a record by id from the URL must ALWAYS include `AND company_id = %s`.
  A record belonging to another company returns 404 (not 403 — do not reveal it exists).
- Any new route or query requires a matching tenant-isolation test (see Testing).

### 2. SQL safety
- Always use parameterized queries. **Never** build SQL with f-strings, `.format()`, or `+`.
- Schema changes go in a new numbered migration file (`migrations/NNN_description.sql`).
  Never edit a migration that has already been committed.

### 3. Users are never deleted
- Deactivate users by setting `is_active = false`. Deactivated users cannot log in, but their
  name remains on every record they signed.
- Do not hard-delete inspections, work orders, or maintenance records tied to compliance.
  Deletion of these (when allowed) must be a soft delete unless a task explicitly says otherwise.

### 4. Signed records (compliance)
Records store who did the work as user ids, never free text:
- **Inspections** (any type) and **maintenance logs**: `performed_by_user_id` ("Inspector" /
  "Performed by"). Always required.
- **Work orders**: `created_by_user_id` ("Created by", always required) and
  `performed_by_user_id` ("Completed by", required whenever the status is closed — closing,
  editing to closed, or creating as closed; enforced by a CHECK constraint too). Reopening clears
  `closed_at` and `performed_by_user_id`. `assigned_to_user_id` ("Assigned to") is optional.
- Each is a dropdown of the company's active users (not free text), pre-selected to the
  logged-in user, and freely changeable — on create and on edit (covers someone using another
  person's device, or a manager filling out a work order on behalf of a mechanic). No
  confirmation step.
- There is **no hidden or automatic "entered by" field**. The dropdown value is the record.
- Build every people dropdown with `people.people_choices()` and validate every submitted user
  id with `people.validate_person()`: it must be an active user in the same company. When
  editing, a user already stored on the record stays listed (marked inactive) and may be kept
  even if since deactivated, so an edit never silently re-signs a record.

### 5. Roles and permissions
Each user belongs to exactly one company and has exactly one role.

| Permission                                      | owner | manager | mechanic | driver |
|-------------------------------------------------|:-----:|:-------:|:--------:|:------:|
| Billing, delete company, transfer ownership     |  ✅   |         |          |        |
| Invite / edit / deactivate users, change roles  |  ✅   |         |          |        |
| Add / edit vehicles                             |  ✅   |   ✅    |          |        |
| Work orders, inventory, maintenance logs        |  ✅   |   ✅    |    ✅    |        |
| Full inspections (incl. annual)                 |  ✅   |   ✅    |    ✅    |        |
| Pre-trip DVIR                                   |  ✅   |   ✅    |    ✅    |   ✅   |
| Dashboard and audit reports                     |  ✅   |   ✅    |    ✅    |  read  |

- Exactly one `owner` per company. Ownership can be transferred to another active user
  (the old owner becomes `manager`).
- Enforce permissions **server-side** with a decorator (e.g. `@role_required("owner", "manager")`).
  Hiding a button in the template is UX only — it is never the security control.

## Accounts and authentication

- Signup creates a company AND its owner account in one step. The signup page shows a note
  that the owner account has full permissions (including deleting data) and is not
  recommended for day-to-day use.
- **Every user, in every role, has their own account and logs in with email + password.**
  `email` is required and unique. There are no shared, role-based, or PIN-only logins.
  (Client companies provide employees an email address if they don't have one.)
- Invites: owner enters email + role → system emails a single-use link, expires in 7 days.
  Store only a hash of the invite token. Owner can resend or revoke pending invites.
- Passwords hashed with werkzeug (`generate_password_hash`).
- All forms carry CSRF protection. Login is rate-limited.
- Config and secrets come from environment variables. Never hardcode secrets.
  Never run with `debug=True` outside local development.

## Data conventions

- Database access: `get_db()` returns one psycopg connection per request, stored on `flask.g`,
  with dict rows; it's closed (rolled back if the request raised) by a teardown handler. Routes
  never close it; they call `db.commit()` after writes. Use `%s` placeholders and
  `INSERT ... RETURNING id`; catch `psycopg.errors.UniqueViolation` for duplicates.
- **Composite foreign keys**: tables that others point to have `UNIQUE (id, company_id)`, and
  every child FK — including user references such as `(performed_by_user_id, company_id)` —
  is `(parent_id, company_id) REFERENCES parent (id, company_id)`. The database refuses
  cross-company links. Every `company_id` and FK column is indexed.
- `dtc_reference` is the **one global table** (shared reference data, no `company_id`).
- **Soft deletes**: work orders, inspection reports and maintenance logs have `deleted_at`;
  vehicles have `archived_at`. Every query excludes soft-deleted rows (`deleted_at IS NULL`):
  lists, detail pages (404), dashboard, audit. Archived vehicles are excluded from vehicle
  lists and dropdowns. Work order attachments stay on disk when a work order is soft-deleted.
- The **USDOT number is per vehicle** (`dot_details.dot_number`): one company can run trucks
  under several DOT numbers. Don't add a company-level DOT number.
- Companies have `company_type` ('fleet' | 'shop'). Only 'fleet' is implemented for now.
- VINs are unique per company: `UNIQUE (company_id, vin)` — not globally unique.
- Vehicles are identified to users by fleet `unit_number` first, then year/make/model.
- Timestamps stored in UTC (`TIMESTAMPTZ`); dates that are calendar dates (due dates,
  inspection dates) stored as `DATE`. Postgres returns `date`/`datetime` objects, not strings:
  compare them as dates, and format in templates with the `|date` / `|datetime` filters (never
  slice). Use `datetime.now(timezone.utc)`, never `utcnow()`. Money is `NUMERIC(10,2)` (Decimal).
- Uploaded files: randomized stored filenames (UUID), served only through an authenticated,
  company-scoped download route — never from `static/`.

## Domain notes

- **DVIR** — Driver Vehicle Inspection Report (49 CFR 396.11); signed by the driver.
- **Annual inspection** — 49 CFR 396.17. Inspector qualification (396.19) is the client
  company's responsibility. **Do not build qualification tracking or restrict who can sign
  which inspection type** beyond the role permissions above.
- Heavy-duty diagnostic codes are **J1939 SPN/FMI**, not passenger-car OBD-II P-codes.
- "Unit number" is the fleet's own identifier for a truck/trailer (e.g. "Unit 42").

## Testing

- Run `pytest` before declaring any task done. All tests must pass.
- **Tenant isolation tests are mandatory**: fixtures create two companies (A and B) with data;
  tests confirm that a user of company B gets a 404 on every company-A resource for every
  route (view, edit, delete, download).
- Role tests: each restricted route rejects roles that lack permission.

## How to work

- Work on a feature branch, never directly on `main`.
- Do only what the task asks. If you notice other problems, list them at the end instead of
  fixing them unasked.
- Keep the app runnable at the end of every task.
- Prefer clear, boring code over clever code. The owner of this repo maintains it part-time.
- When finished, give a short summary: what changed, files touched, how to test it manually,
  and anything that needs a human decision.
- If you make a decision that future tasks depend on, propose an update to this file.
