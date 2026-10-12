"""Settings read from environment variables (or a local .env file)."""
import os

from dotenv import load_dotenv
from psycopg.conninfo import make_conninfo

# Project root (the folder above this package). Default paths are anchored here so they
# don't depend on the directory the app happens to be started from.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Load settings from a local .env file if there is one. Real environment variables win.
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))


def path_setting(name, default):
    """Read a filesystem path from the environment; relative paths are resolved
    against the project root, not the current working directory."""
    return os.path.join(PROJECT_ROOT, os.environ.get(name) or default)


def _postgres_conninfo(dbname):
    """Connection string for the local docker-compose Postgres, from the POSTGRES_* settings."""
    return make_conninfo(
        host="localhost",
        port=5432,
        dbname=dbname,
        user=os.environ.get("POSTGRES_USER"),
        password=os.environ.get("POSTGRES_PASSWORD"),
    )


def database_url():
    return os.environ.get("DATABASE_URL") or _postgres_conninfo(os.environ.get("POSTGRES_DB"))


def test_database_url():
    return os.environ.get("TEST_DATABASE_URL") or _postgres_conninfo(f"{os.environ.get('POSTGRES_DB')}_test")
