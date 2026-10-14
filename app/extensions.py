"""Flask extensions that blueprints need to reference at import time
(e.g. @limiter.limit on a route). They're attached to the app in create_app()."""
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect

# CSRF only — forms stay plain HTML. Every <form method="post"> carries
#   <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
csrf = CSRFProtect()

# Keyed by client IP. Storage comes from RATELIMIT_STORAGE_URI (memory:// by default,
# which is per-process: use shared storage such as Redis when running several instances).
# NOTE: behind a reverse proxy, remote_addr is the proxy — add ProxyFix at deploy time.
limiter = Limiter(key_func=get_remote_address)
