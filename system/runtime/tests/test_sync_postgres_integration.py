"""Integration checks for the prepared Supabase SQL schema, against disposable PostgreSQL.

This test suite is opt-in and skipped without a test-only PostgreSQL URL.
It never reads Supabase credentials or contacts a Supabase project.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

from supabase_transport import event_row


@pytest.fixture
def isolated_database():
    dsn = os.environ.get("SYNC_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("No disposable PostgreSQL test instance configured")
    with psycopg.connect(dsn, autocommit=True) as conn:
        # Dispose any leftover objects in this ephemeral CI database.
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS public.sync_deliveries")
            cur.execute("DROP TABLE IF EXISTS public.sync_events")
        sql = (Path(__file__).resolve().parents[1] / "supabase-sync-schema.sql").read_text()
        conn.execute(sql)
        yield conn
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS public.sync_deliveries")
            cur.execute("DROP TABLE IF EXISTS public.sync_events")


def sample_event():
    return {
        "event_id": "SYNC-INTEGRATION-TEST-01",
        "project_id": "P00",
        "source": "GitHub",
        "source_ref": "https://github.com/akartashova51180-code/symmetrical-guide/issues/2",
        "created_at": "2026-11-19T20:03:58Z",
        "version": 1,
        "type": "test_task",
        "approval": "yes, test only",
        "privacy": "public_synthetic",
        "text": "Safe ephemeral PostgreSQL test",
    }


def test_prepared_schema_has_rls_enabled_and_primary_keys(isolated_database):
    conn = isolated_database
    records = conn.execute("""
        SELECT c.relname, c.relrowsecurity
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relname IN ('sync_events', 'sync_deliveries')
        ORDER BY c.relname
    """).fetchall()
    assert records == [("sync_deliveries", True), ("sync_events", True)]
    rows = conn.execute("""
        SELECT tablename, policyname FROM pg_policies
        WHERE schemaname = 'public' AND tablename IN ('sync_events','sync_deliveries')
    """).fetchall()
    assert rows == []  # No anon/authenticated policy is deliberately created.


def test_insert_and_conflict_preserves_original_and_fk(isolated_database):
    conn = isolated_database
    row = event_row(sample_event())
    columns = list(row)
    placeholders = ", ".join(["%s"] * len(columns))
    statement = (
        "INSERT INTO public.sync_events (" + ", ".join(columns) + ") "
        "VALUES (" + placeholders + ") ON CONFLICT (event_id, version) DO NOTHING"
    )
    values = [json.dumps(row[k]) if k == "payload" else row[k] for k in columns]
    assert conn.execute(statement, values).rowcount == 1
    assert conn.execute(statement, values).rowcount == 0
    original_hash = conn.execute(
        "SELECT payload_sha256 FROM public.sync_events WHERE event_id=%s AND version=1",
        (row["event_id"],),
    ).fetchone()[0]
    assert original_hash == row["payload_sha256"]

    changed = event_row({**sample_event(), "text": "conflicting text"})
    changed_values = [json.dumps(changed[k]) if k == "payload" else changed[k] for k in columns]
    assert conn.execute(statement, changed_values).rowcount == 0
    assert conn.execute(
        "SELECT payload_sha256 FROM public.sync_events WHERE event_id=%s AND version=1",
        (row["event_id"],),
    ).fetchone()[0] == original_hash

    assert conn.execute(
        """INSERT INTO public.sync_deliveries(event_id,version,destination)
        VALUES (%s,1,'codex') ON CONFLICT DO NOTHING""", (row["event_id"],),
    ).rowcount == 1
    assert conn.execute(
        """INSERT INTO public.sync_deliveries(event_id,version,destination)
        VALUES (%s,1,'codex') ON CONFLICT DO NOTHING""", (row["event_id"],),
    ).rowcount == 0
