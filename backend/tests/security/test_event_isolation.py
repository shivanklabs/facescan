"""Invariants 1 and 2: the database itself blocks reads and writes across events (RLS).

Every query below is raw SQL on the facescan role's connection, so nothing in the app's
service layer is involved: if these pass, a forgotten WHERE event_id = ... cannot leak data.
"""

import uuid

import pytest
from app.core.context import AppContext
from app.db.event_scope import (
    ADMIN_SCOPE,
    DISPATCH_SCOPE,
    event_transaction,
    scoped_transaction,
)
from app.db.models import EVENT_SCOPED_TABLES
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from tests.helpers import quick_event


def two_events_with_data(ctx: AppContext) -> tuple[uuid.UUID, uuid.UUID]:
    a = quick_event(ctx, "Event A")
    b = quick_event(ctx, "Event B")
    for event_id in (a, b):
        with event_transaction(ctx.sessions, event_id) as s:
            s.execute(
                text("INSERT INTO collections (id, event_id) VALUES (:id, :e)"),
                {"id": uuid.uuid4(), "e": event_id},
            )
    return a, b


def test_app_role_cannot_bypass_rls(ctx: AppContext) -> None:
    with ctx.engine.connect() as conn:
        row = conn.execute(
            text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        ).one()
    assert row == (False, False)


def test_every_event_table_has_forced_rls_and_a_policy(ctx: AppContext, db_schema: str) -> None:
    with ctx.engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, "
                "(SELECT count(*) FROM pg_policies p WHERE p.schemaname = :s "
                " AND p.tablename = c.relname) "
                "FROM pg_class c WHERE c.relnamespace = to_regnamespace(:s) AND c.relkind = 'r'"
            ),
            {"s": db_schema},
        ).all()
    info = {r[0]: (r[1], r[2], r[3]) for r in rows}
    for table in EVENT_SCOPED_TABLES:
        enabled, forced, policies = info[table]
        assert enabled and forced and policies >= 1, table


def test_reads_are_blocked_across_events(ctx: AppContext) -> None:
    a, b = two_events_with_data(ctx)
    with event_transaction(ctx.sessions, a) as s:
        assert s.execute(text("SELECT count(*) FROM collections")).scalar() == 1
        assert (
            s.execute(
                text("SELECT count(*) FROM collections WHERE event_id = :b"), {"b": b}
            ).scalar()
            == 0
        )
        assert s.execute(text("SELECT count(*) FROM events")).scalar() == 1
        assert s.execute(text("SELECT count(*) FROM event_keys")).scalar() == 1


def test_writes_are_blocked_across_events(ctx: AppContext) -> None:
    a, b = two_events_with_data(ctx)
    with event_transaction(ctx.sessions, a) as s:
        updated = s.execute(
            text("UPDATE collections SET status = 'approved' WHERE event_id = :b"), {"b": b}
        ).rowcount
        deleted = s.execute(text("DELETE FROM event_keys WHERE event_id = :b"), {"b": b}).rowcount
        assert (updated, deleted) == (0, 0)
    with (
        pytest.raises(ProgrammingError, match="row-level security"),
        event_transaction(ctx.sessions, a) as s,
    ):
        s.execute(
            text("INSERT INTO collections (id, event_id) VALUES (:id, :b)"),
            {"id": uuid.uuid4(), "b": b},
        )
    with (
        pytest.raises(ProgrammingError, match="row-level security"),
        event_transaction(ctx.sessions, a) as s,
    ):  # moving a row to another event
        s.execute(text("UPDATE collections SET event_id = :b"), {"b": b})
    with event_transaction(ctx.sessions, b) as s:
        assert (
            s.execute(text("SELECT count(*) FROM collections WHERE status = 'open'")).scalar() == 1
        )
        assert s.execute(text("SELECT count(*) FROM event_keys")).scalar() == 1


def test_no_scope_sees_nothing(ctx: AppContext) -> None:
    two_events_with_data(ctx)
    with ctx.sessions() as s, s.begin():
        for table in EVENT_SCOPED_TABLES:
            assert s.execute(text(f"SELECT count(*) FROM {table}")).scalar() == 0, table  # noqa: S608
        s.execute(text("SELECT set_config('app.event_id', '', true)"))
        assert s.execute(text("SELECT count(*) FROM events")).scalar() == 0


def test_admin_and_dispatch_scopes_never_see_event_data(ctx: AppContext) -> None:
    a, _ = two_events_with_data(ctx)
    with scoped_transaction(ctx.sessions, ADMIN_SCOPE) as s:
        assert s.execute(text("SELECT count(*) FROM events")).scalar() >= 2
        for table in (
            "photos",
            "faces",
            "clusters",
            "event_keys",
            "collections",
            "event_calibrations",
            "jobs",
        ):
            assert s.execute(text(f"SELECT count(*) FROM {table}")).scalar() == 0, table  # noqa: S608
        assert s.execute(text("UPDATE events SET name = 'x'")).rowcount == 0  # read-only
    with event_transaction(ctx.sessions, a) as s:
        ctx.queue.enqueue(s, a, "noop", {})
    with scoped_transaction(ctx.sessions, DISPATCH_SCOPE) as s:
        assert s.execute(text("SELECT count(*) FROM jobs")).scalar() >= 1
        for table in ("photos", "faces", "events", "event_keys", "collections"):
            assert s.execute(text(f"SELECT count(*) FROM {table}")).scalar() == 0, table  # noqa: S608


def test_rls_cannot_be_switched_off(ctx: AppContext) -> None:
    a, _ = two_events_with_data(ctx)
    with (
        pytest.raises(DBAPIError, match="row-level security"),
        event_transaction(ctx.sessions, a) as s,
    ):
        s.execute(text("SET LOCAL row_security = off"))
        s.execute(text("SELECT count(*) FROM collections")).scalar()


def test_audit_log_is_append_only(ctx: AppContext) -> None:
    from app.features.activity_log.service import record

    a, _ = two_events_with_data(ctx)
    with event_transaction(ctx.sessions, a) as s:
        record(s, event_id=a, actor="test", action="test.row")
    with pytest.raises(DBAPIError, match="append-only"), event_transaction(ctx.sessions, a) as s:
        s.execute(text("UPDATE audit_log SET action = 'tampered'"))
    with pytest.raises(DBAPIError, match="append-only"), event_transaction(ctx.sessions, a) as s:
        s.execute(text("DELETE FROM audit_log"))
