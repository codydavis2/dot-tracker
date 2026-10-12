"""Guarantees enforced by the database itself (migrations/001_initial.sql), checked
with direct SQL so they hold even if application code gets a query wrong."""
import psycopg
import pytest
from psycopg import sql

from conftest import make_company, make_user


@pytest.fixture
def two_companies(query, users):
    """A vehicle and a work order in each company."""
    ids = dict(users)
    for key in ("a", "b"):
        ids[f"{key}_vehicle"] = query(
            "INSERT INTO vehicles (company_id, make, model, year) VALUES (%s, 'Mack', 'Anthem', 2021) RETURNING id",
            (users[f"{key}_company"],),
        )[0]["id"]
        ids[f"{key}_work_order"] = query(
            "INSERT INTO work_orders (company_id, vehicle_id, created_by_user_id) VALUES (%s, %s, %s) RETURNING id",
            (users[f"{key}_company"], ids[f"{key}_vehicle"], users[f"{key}_owner"]),
        )[0]["id"]
    return ids


def test_work_order_cannot_point_at_another_companys_vehicle(query, two_companies):
    ids = two_companies
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        query(
            "INSERT INTO work_orders (company_id, vehicle_id, created_by_user_id) VALUES (%s, %s, %s)",
            (ids["a_company"], ids["b_vehicle"], ids["a_owner"]),
        )


def test_work_order_cannot_be_moved_to_another_companys_vehicle(query, two_companies):
    ids = two_companies
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        query("UPDATE work_orders SET vehicle_id = %s WHERE id = %s", (ids["b_vehicle"], ids["a_work_order"]))


@pytest.mark.parametrize("column", ["created_by_user_id", "assigned_to_user_id", "performed_by_user_id"])
def test_work_order_people_must_be_in_same_company(query, two_companies, column):
    ids = two_companies
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        query(sql.SQL("UPDATE work_orders SET {} = %s WHERE id = %s").format(sql.Identifier(column)),
              (ids["b_mechanic"], ids["a_work_order"]))


def test_child_rows_cannot_cross_companies(query, two_companies):
    ids = two_companies
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        query(
            "INSERT INTO work_order_parts (company_id, work_order_id, part_name) VALUES (%s, %s, 'Filter')",
            (ids["a_company"], ids["b_work_order"]),
        )
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        query(
            """
            INSERT INTO inspection_reports (company_id, vehicle_id, performed_by_user_id)
            VALUES (%s, %s, %s)
            """,
            (ids["a_company"], ids["a_vehicle"], ids["b_owner"]),
        )


def test_only_one_owner_per_company(query, users):
    with pytest.raises(psycopg.errors.UniqueViolation):
        make_user(query, users["a_company"], "second-owner@a.example.com", "Second Owner", role="owner")


def test_each_company_has_its_own_owner(query, users):
    company_id = make_company(query, "Company C")
    make_user(query, company_id, "owner@c.example.com", "Owner C", role="owner")


def test_closed_work_order_requires_completed_by(query, two_companies):
    with pytest.raises(psycopg.errors.CheckViolation):
        query("UPDATE work_orders SET status = 'closed' WHERE id = %s", (two_companies["a_work_order"],))


def test_emails_are_stored_lowercased_and_unique(query, users):
    with pytest.raises(psycopg.errors.CheckViolation):
        make_user(query, users["a_company"], "Mixed@Example.com", "Mixed Case")
    with pytest.raises(psycopg.errors.UniqueViolation):
        make_user(query, users["b_company"], "owner@a.example.com", "Duplicate")


def test_every_table_but_dtc_reference_has_indexed_company_id(query):
    tables = {r["table_name"] for r in query(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
    )} - {"schema_migrations", "companies"}
    with_company_id = {r["table_name"]: r["is_nullable"] for r in query(
        """
        SELECT table_name, is_nullable FROM information_schema.columns
        WHERE table_schema = 'public' AND column_name = 'company_id'
        """
    )}
    assert tables - set(with_company_id) == {"dtc_reference"}
    assert set(with_company_id.values()) == {"NO"}

    indexed = {r["table_name"] for r in query(
        """
        SELECT t.relname AS table_name
        FROM pg_index i
        JOIN pg_class t ON t.oid = i.indrelid
        JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = i.indkey[0]
        WHERE a.attname = 'company_id'
        """
    )}
    assert set(with_company_id) - indexed == set()
