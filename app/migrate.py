"""Tiny SQL migration runner, exposed as `flask migrate`.

Migrations are plain SQL files in migrations/ named NNN_description.sql. Each
file is applied once, in filename order, inside its own transaction, and
recorded in the schema_migrations table. Never edit a file that has already
been committed — add a new one instead.
"""
import os
import re

import click
import psycopg
from flask import current_app

from .config import PROJECT_ROOT

MIGRATIONS_DIR = os.path.join(PROJECT_ROOT, "migrations")
FILENAME_PATTERN = re.compile(r"^\d{3}_[a-z0-9_]+\.sql$")


def migration_files():
    names = sorted(name for name in os.listdir(MIGRATIONS_DIR) if name.endswith(".sql"))
    for name in names:
        if not FILENAME_PATTERN.match(name):
            raise click.ClickException(f"Bad migration filename {name!r}; expected NNN_description.sql")
    return names


def apply_migrations(database_url, echo=print):
    """Apply every migration not yet recorded in schema_migrations. Returns the names applied."""
    applied_now = []
    # autocommit, so each conn.transaction() below is a real BEGIN ... COMMIT per file
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                filename TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        already_applied = {row[0] for row in conn.execute("SELECT filename FROM schema_migrations")}

        for name in migration_files():
            if name in already_applied:
                continue
            with open(os.path.join(MIGRATIONS_DIR, name)) as f:
                sql = f.read()
            echo(f"Applying {name}")
            with conn.transaction():
                conn.execute(sql)
                conn.execute("INSERT INTO schema_migrations (filename) VALUES (%s)", (name,))
            applied_now.append(name)
    return applied_now


@click.command("migrate")
def migrate_command():
    """Apply any new SQL files in migrations/ to the database."""
    applied = apply_migrations(current_app.config["DATABASE_URL"], echo=click.echo)
    click.echo(f"Applied {len(applied)} migration(s)." if applied else "Database is up to date.")


def init_app(app):
    app.cli.add_command(migrate_command)
