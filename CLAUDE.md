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

- App code lives in the nested `dot-tracker/` folder (app.py, helpers.py, schema.sql, templates/, static/).
- Currently SQLite with single-user ownership (`user_id` on data tables). Being refactored to
  multi-tenant Postgres with `company_id`. Until that refactor lands, do not add new features
  on top of the old `user_id` model.
- `app.py` is ~1,300 lines and is planned to be split into Flask blueprints.

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
Every inspection (any type) and every work order completion stores
`performed_by_user_id` — the person who did the inspection/repair.
- **Required.** The form cannot be submitted without it.
- Rendered as a dropdown of the company's active users (not free text), pre-selected to the
  logged-in user, and freely changeable. No confirmation step.
- Common case: a manager filling out a work order on behalf of a mechanic.
- The dropdown must only list active users from the same company; validate this server-side.

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

- Companies have `company_type` ('fleet' | 'shop'). Only 'fleet' is implemented for now.
- VINs are unique per company: `UNIQUE (company_id, vin)` — not globally unique.
- Vehicles are identified to users by fleet `unit_number` first, then year/make/model.
- Timestamps stored in UTC (`TIMESTAMPTZ`); dates that are calendar dates (due dates,
  inspection dates) stored as `DATE`.
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
