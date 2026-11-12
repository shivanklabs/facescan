"""Row-level security scope for each transaction (invariants 1 and 2).

Every event-scoped table has RLS forced on, with policies keyed on
current_setting('app.event_id'). The app sets the scope per transaction (set_config(..., true)
is SET LOCAL), so it ends with the transaction and never leaks between requests:

- event scope: app.event_id = <uuid>. The only scope that can read or write event data.
- admin scope: app.scope = 'admin'. Read-only listing of event metadata (events table) and
  writing audit rows without an event. Never grants access to photos, faces or keys.
- dispatch scope: app.scope = 'dispatch'. Lets the worker see and claim rows of the jobs table
  (no biometric data) before it knows which event a job belongs to.
- user scope: app.user_id = <uuid>. Read-only: the signed-in person's own event_members rows,
  own consents, and the events they belong to ("my events", privacy settings). Can be combined
  with the event scope; it never opens photos, faces, matches or keys of anyone else.

With no scope set, every policy evaluates to false and nothing is visible.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

ADMIN_SCOPE = "admin"
DISPATCH_SCOPE = "dispatch"


def set_event_scope(session: Session, event_id: UUID) -> None:
    """Purpose: bind the current transaction to one event. Side effects: SET LOCAL."""
    session.execute(
        text("SELECT set_config('app.event_id', :event_id, true)"), {"event_id": str(event_id)}
    )


def set_user_scope(session: Session, user_id: UUID) -> None:
    """Purpose: let this transaction read the user's own memberships and consents."""
    session.execute(
        text("SELECT set_config('app.user_id', :user_id, true)"), {"user_id": str(user_id)}
    )


def set_named_scope(session: Session, scope: str) -> None:
    """Purpose: enable the admin or dispatch scope for this transaction. Side effects: SET LOCAL."""
    if scope not in (ADMIN_SCOPE, DISPATCH_SCOPE):
        raise ValueError("unknown scope")
    session.execute(text("SELECT set_config('app.scope', :scope, true)"), {"scope": scope})


@contextmanager
def event_transaction(
    factory: sessionmaker[Session], event_id: UUID, user_id: UUID | None = None
) -> Iterator[Session]:
    """
    Purpose: a transaction that can only see and change one event's rows (plus, when user_id
        is given, read the user's own memberships and consents).
    Outputs: session (committed on success, rolled back on error).
    """
    with factory() as session, session.begin():
        set_event_scope(session, event_id)
        if user_id is not None:
            set_user_scope(session, user_id)
        yield session


@contextmanager
def user_transaction(factory: sessionmaker[Session], user_id: UUID) -> Iterator[Session]:
    """Purpose: a transaction in the read-only user scope (no event scope)."""
    with factory() as session, session.begin():
        set_user_scope(session, user_id)
        yield session


@contextmanager
def scoped_transaction(factory: sessionmaker[Session], scope: str) -> Iterator[Session]:
    """Purpose: a transaction in the admin or dispatch scope (see module docstring)."""
    with factory() as session, session.begin():
        set_named_scope(session, scope)
        yield session
