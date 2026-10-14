"""Single-use tokens for emailed links (password resets, invites).

The raw token only ever appears in the emailed link. The database stores its
SHA-256 hex digest, so a leaked database backup can't be used to reset a
password or accept an invite. Looking a token up = hashing what's in the URL
and searching for that hash.
"""
import hashlib
import secrets


def new_token():
    """Return (raw_token, token_hash). Email the raw token; store only the hash."""
    raw = secrets.token_urlsafe(32)
    return raw, hash_token(raw)


def hash_token(raw):
    return hashlib.sha256(raw.encode()).hexdigest()
