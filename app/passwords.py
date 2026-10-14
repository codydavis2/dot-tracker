"""Password policy — the one validator used everywhere a password is set
(signup, password reset, invite accept, change password).

Length-based, no composition rules ("must include a symbol"), in line with
NIST SP 800-63B: 8–128 characters, not a very common password, not your email.
Checked only when a password is SET; existing passwords keep working at login.
"""
import os

PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128


def _load_common_passwords():
    path = os.path.join(os.path.dirname(__file__), "common_passwords.txt")
    with open(path) as f:
        return {
            line.strip().lower()
            for line in f
            if line.strip() and not line.startswith("#")
        }


COMMON_PASSWORDS = _load_common_passwords()


def password_error(password, email):
    """Return an error message if the password breaks the policy, else None."""
    password = password or ""
    if len(password) < PASSWORD_MIN_LENGTH:
        return f"Password must be at least {PASSWORD_MIN_LENGTH} characters."
    if len(password) > PASSWORD_MAX_LENGTH:
        return f"Password must be at most {PASSWORD_MAX_LENGTH} characters."
    if password.lower() in COMMON_PASSWORDS:
        return "That password is too common. Choose something harder to guess."
    if email and password.strip().lower() == email.strip().lower():
        return "Password can't be the same as your email address."
    return None
