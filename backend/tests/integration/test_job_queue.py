"""Job queue: SKIP LOCKED claiming, retries with backoff, failure state, stale-lock recovery."""

import threading
import time
import uuid

from app.core.context import AppContext
from app.db.event_scope import DISPATCH_SCOPE, event_transaction, scoped_transaction
from app.modules.events.service import create_event
from sqlalchemy import text


def drain(ctx: AppContext) -> None:
    """Mark leftover jobs from other tests in this schema as done."""
    with scoped_transaction(ctx.sessions, DISPATCH_SCOPE) as s:
        s.execute(text("UPDATE jobs SET status = 'done' WHERE status IN ('queued', 'running')"))


def enqueue(ctx: AppContext, n: int, job_type: str = "noop") -> tuple[uuid.UUID, list[uuid.UUID]]:
    event_id = create_event(ctx, "Jobs", "IN", "test").event_id
    with event_transaction(ctx.sessions, event_id) as s:
        ids = [ctx.queue.enqueue(s, event_id, job_type, {"n": i}) for i in range(n)]
    return event_id, ids


def test_two_workers_never_take_the_same_job(ctx: AppContext) -> None:
    drain(ctx)
    _, ids = enqueue(ctx, 40)
    taken: dict[str, list[uuid.UUID]] = {"w1": [], "w2": []}
    start = threading.Barrier(2)

    def worker(name: str) -> None:
        start.wait()
        while (job := ctx.queue.claim(name)) is not None:
            taken[name].append(job.job_id)
            time.sleep(0.001)  # hold the job briefly so both workers overlap

    threads = [threading.Thread(target=worker, args=(n,)) for n in taken]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    all_taken = taken["w1"] + taken["w2"]
    assert sorted(all_taken) == sorted(ids)  # every job exactly once
    assert not set(taken["w1"]) & set(taken["w2"])
    assert taken["w1"] and taken["w2"]  # both workers really competed


def test_retry_with_backoff_then_failed(ctx: AppContext) -> None:
    drain(ctx)
    event_id, (job_id,) = enqueue(ctx, 1)
    job = ctx.queue.claim("w")
    assert job is not None and job.job_id == job_id and job.attempts == 1
    assert ctx.queue.fail(job, "ValueError") is True  # will retry
    with event_transaction(ctx.sessions, event_id) as s:
        row = s.execute(
            text("SELECT status, last_error, run_after > now() AS later FROM jobs")
        ).one()
    assert (row.status, row.last_error, row.later) == ("queued", "ValueError", True)
    attempts = 1
    while True:
        time.sleep(0.05)
        job = ctx.queue.claim("w")
        if job is None:
            continue
        attempts = job.attempts
        if not ctx.queue.fail(job, "ValueError"):
            break
    assert attempts == ctx.settings.job_max_attempts
    with event_transaction(ctx.sessions, event_id) as s:
        assert s.execute(text("SELECT status FROM jobs")).scalar() == "failed"
    assert ctx.queue.claim("w") is None  # failed jobs are never claimed again


def test_complete_and_stale_lock_recovery(ctx: AppContext) -> None:
    drain(ctx)
    event_id, (job_id,) = enqueue(ctx, 1)
    job = ctx.queue.claim("crashed-worker")
    assert job is not None
    assert ctx.queue.claim("other") is None  # running and fresh: not claimable
    with event_transaction(ctx.sessions, event_id) as s:
        s.execute(text("UPDATE jobs SET locked_at = now() - interval '2 hours'"))
    again = ctx.queue.claim("other")
    assert again is not None and again.job_id == job_id and again.attempts == 2
    ctx.queue.complete(again)
    with event_transaction(ctx.sessions, event_id) as s:
        assert s.execute(text("SELECT status FROM jobs")).scalar() == "done"
