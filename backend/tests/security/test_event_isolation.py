"""Invariants 1 and 2: no query can read another event's photos, faces, clusters or claims."""

import pytest


@pytest.mark.skip(reason="TODO: needs RLS policies and repositories")
def test_rls_blocks_cross_event_reads() -> None:
    # TODO: create two events, set app.event_id to event A, assert event B rows are invisible
    # for every event-scoped table, including via raw SQL.
    pass
