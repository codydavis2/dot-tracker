-- M2 — accounts and security: "log out everywhere", password reset tokens, invites.
--
-- Tokens (reset and invite) are never stored: only their SHA-256 hex digest is.
-- The raw token exists only in the emailed link.

-- Bumped on password change, password reset and deactivation. The value is copied
-- into the session at login; a session whose copy no longer matches is logged out.
ALTER TABLE users ADD COLUMN auth_version INTEGER NOT NULL DEFAULT 1;

-- One row per reset request. Valid while used_at IS NULL AND expires_at > now().
-- A newer request expires the older unused ones (expires_at is set to now()).
CREATE TABLE password_resets (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    company_id BIGINT NOT NULL REFERENCES companies (id),
    user_id    BIGINT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    used_at    TIMESTAMPTZ,
    FOREIGN KEY (user_id, company_id) REFERENCES users (id, company_id)
);

-- Invites to join a company. Pending = accepted_at IS NULL AND revoked_at IS NULL
-- (an expired pending invite can be resent, which issues a new token and expiry).
-- Never 'owner': ownership transfer is a separate flow.
CREATE TABLE invites (
    id                 BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    company_id         BIGINT NOT NULL REFERENCES companies (id),
    email              TEXT NOT NULL CHECK (email = lower(btrim(email))),
    role               TEXT NOT NULL CHECK (role IN ('manager', 'mechanic', 'driver')),
    token_hash         TEXT NOT NULL UNIQUE,
    invited_by_user_id BIGINT NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at         TIMESTAMPTZ NOT NULL,
    accepted_at        TIMESTAMPTZ,
    revoked_at         TIMESTAMPTZ,
    UNIQUE (id, company_id),
    FOREIGN KEY (invited_by_user_id, company_id) REFERENCES users (id, company_id),
    CHECK (accepted_at IS NULL OR revoked_at IS NULL)
);
-- Only one pending invite per (company, email)
CREATE UNIQUE INDEX invites_one_pending_per_email ON invites (company_id, email)
    WHERE accepted_at IS NULL AND revoked_at IS NULL;

CREATE INDEX password_resets_company_id_idx ON password_resets (company_id);
CREATE INDEX password_resets_user_id_idx    ON password_resets (user_id);
CREATE INDEX invites_company_id_idx         ON invites (company_id);
CREATE INDEX invites_invited_by_idx         ON invites (invited_by_user_id);
