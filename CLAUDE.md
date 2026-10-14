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
  `create_app()` factory and one blueprint per feature: `auth` (login/logout/signup/password
  reset), `dashboard`, `vehicles` (incl. maintenance logs + DTCs), `inspections` (reports +
  reminders), `work_orders` (incl. attachments), `inventory`, `audit`, `users` (owner's user
  management), `invites`, `account` (My Account). URL paths are hard-coded in templates and
  redirects (no `url_for`).
- **PostgreSQL** (docker-compose, `docker compose up -d`) via psycopg 3. Schema lives in
  numbered SQL migrations in `migrations/` (`001_initial.sql` is the whole M1 schema,
  `002_accounts_security.sql` adds `users.auth_version`, `password_resets`, `invites`), applied
  with `flask migrate`. There is no schema.sql and no SQLite anymore.
- **Multi-tenant** (M1 done): every customer-data table has `company_id`; every query is scoped
  to `g.user["company_id"]`. Signup (`/register`) creates a company and its owner together.
- **Accounts and security** (M2 done): CSRF on every form, POST-only logout, session rotation
  at login, `auth_version` "log out everywhere", rate limiting, password policy, password reset,
  invites, owner-only `/users` page, `/account` page for everyone. Details under "Accounts and
  authentication" below.
- **Email + password login.** The session stores only `user_id` and `auth_version`;
  `auth.load_logged_in_user` reloads the user into `g.user` on every request and logs out
  missing/deactivated users and stale sessions.
- **Roles are enforced only on the user-management and invite-management routes**
  (`@role_required("owner")`). Everything else is still open to every logged-in role — general
  enforcement per the permissions table is M3. Ownership transfer is also M3.
- Email: console backend only (`MAIL_BACKEND=console` prints messages to the terminal). SES is a
  stub, added at deploy time.
- Not done yet (planned for later milestones): role enforcement across the app, ownership
  transfer, S3, SES.
- `flask seed-demo` creates "Demo Fleet Co" with sample users and data and prints the logins.
- Config comes from environment variables / `.env` (`SECRET_KEY`, `DATABASE_URL`,
  `TEST_DATABASE_URL`, `UPLOAD_DIR`, `FLASK_DEBUG`, `POSTGRES_*`, `MAIL_BACKEND`, `MAIL_FROM`,
  `RATELIMIT_STORAGE_URI`); see `.env.example`.
- Tests: pytest against a separate Postgres database (`TEST_DATABASE_URL`, wiped on every run),
  with two-company fixtures (A and B, each with an owner and a mechanic). CSRF and rate limiting
  are OFF in the general test config (`make_app(WTF_CSRF_ENABLED=True)` /
  `make_app(RATELIMIT_ENABLED=True)` turn them on for the tests that cover them). The `outbox`
  fixture captures sent email. Known bugs are recorded as `xfail(strict=True)` tests with a
  reason; when fixing one, remove its marker.

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
- Invites: owner enters email + role (manager | mechanic | driver — never owner) → system
  emails a single-use link, expires in 7 days. Owner can resend (new token + new 7 days; the
  old link dies) or revoke pending invites. One pending invite per (company, email). An email
  that belongs to ANY user can't be invited (re-checked at accept time). Accepting creates
  the user in the invite's company with the invite's role and logs them in.
- **Emailed tokens** (password reset and invite): `secrets.token_urlsafe(32)`, made by
  `tokens.new_token()`. Store ONLY the SHA-256 hash (`token_hash`); look up by hashing the
  URL token. Single use. Reset links expire in 1 hour and a new request expires older unused
  ones; invites expire in 7 days.
- **Password reset** never reveals whether an email has an account (same page either way);
  deactivated users get no email.
- **Password policy** — one validator, `passwords.password_error(password, email)`, used
  everywhere a password is set (signup, reset, invite accept, change password):
  8–128 characters (`PASSWORD_MIN_LENGTH` / `PASSWORD_MAX_LENGTH`), NO composition rules,
  not in the bundled `app/common_passwords.txt`, not equal to the user's email. Existing
  passwords still work at login.
- Passwords hashed with werkzeug (`generate_password_hash`).
- **Sessions**: log in only through `auth.start_session(user)` (clears the session, stores
  `user_id` + `auth_version`, rotates the session id). `users.auth_version` is bumped on
  password change, password reset and deactivation, which logs out every session started
  before ("log out everywhere"); a password change keeps the current session by storing the
  new value. Cookies: HttpOnly, SameSite=Lax, Secure unless FLASK_DEBUG is on.
- **CSRF**: Flask-WTF `CSRFProtect` (CSRF only, forms stay plain HTML). Every
  `<form method="post">` includes
  `<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">` — a test checks this.
  Logout is POST-only. Missing/invalid token → friendly 400 page.
- **Rate limiting**: Flask-Limiter (`extensions.limiter`), keyed by client IP, storage from
  `RATELIMIT_STORAGE_URI` (in-memory by default). POST limits: `/login` 10/minute + 50/hour;
  `/register`, `/forgot-password`, `/invite/<token>` 5/minute. Over the limit → friendly 429.
- **Email**: `email.send_email(to, subject, body_text)`; links from `email.absolute_url(path)`
  (current request's root URL). `MAIL_BACKEND=console` prints the message; ONLY in console mode
  `email.show_dev_link(link)` also shows the link on screen. `ses` is a stub (NotImplementedError).
- **Roles**: `helpers.role_required(*roles)` decorator — logged out → /login, wrong role → 403.
  So far used only on the owner-only `/users` and invite-management routes. The owner can't
  change their own role or deactivate themselves; nobody can be made owner on `/users`.
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
- CSRF: a test enumerates `app.url_map` and checks EVERY POST route rejects a missing token,
  so new POST routes are covered automatically — but new forms still need the hidden input.

## Backlog (cleanup milestone after M3)

- Relabel the DOT checkbox "Subject to FMCSA/DOT regulations"
  (generally 10,001+ lbs GVWR in interstate commerce); CDL is
  handled separately by "CDL class required" (A/B/C/none).
- Audit report: archived vehicles selectable, labeled "(archived)".
- Company time zone setting (default America/Los_Angeles); display
  times in it instead of UTC.
- UI to archive a vehicle and to delete an inspection report;
  forms for maintenance logs and DTCs.
- Input validation: no 500s on malformed reminder date, unknown
  reminder type, unknown reminder interval, missing service type,
  or non-numeric numbers.
- Replace passenger-car OBD-II seed codes with J1939 SPN/FMI.
- Production rate-limit storage (shared, e.g. Redis) when deployed
  with more than one instance.
- Revisit password minimum length (currently 8) if two-factor
  login is added or a customer requires a stricter standard.
- Fix the Flask-Session deprecation warning; refresh README
  "What I'd build next".

## How to work

- Work on a feature branch, never directly on `main`.
- Do only what the task asks. If you notice other problems, list them at the end instead of
  fixing them unasked.
- Keep the app runnable at the end of every task.
- Prefer clear, boring code over clever code. The owner of this repo maintains it part-time.
- When finished, give a short summary: what changed, files touched, how to test it manually,
  and anything that needs a human decision.
- If you make a decision that future tasks depend on, propose an update to this file.
