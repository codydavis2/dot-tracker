import psycopg
from flask import current_app, g
from psycopg.rows import dict_row

from .config import NOT_CONFIGURED_MESSAGE


def get_db():
    """Return this request's database connection, opening it on first use.
    Rows come back as dicts. The connection is closed automatically when the
    request ends (see close_db), so routes never close it themselves."""
    if "db" not in g:
        if not current_app.config["DATABASE_URL"]:
            raise RuntimeError(NOT_CONFIGURED_MESSAGE)
        g.db = psycopg.connect(current_app.config["DATABASE_URL"], row_factory=dict_row)
    return g.db


def close_db(exc=None):
    """Close the request's connection. If the request raised, roll back first so a
    half-finished write is never left holding locks."""
    db = g.pop("db", None)
    if db is None:
        return
    try:
        if exc is not None:
            db.rollback()
    finally:
        db.close()


def init_app(app):
    app.teardown_appcontext(close_db)
