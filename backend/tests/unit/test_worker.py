import threading

from app.core.config import Settings
from app.worker import run


def test_worker_idle_loop_runs_and_stops(settings: Settings) -> None:
    assert run(settings, threading.Event(), max_iterations=3) == 3


def test_worker_stops_when_event_set(settings: Settings) -> None:
    stop = threading.Event()
    stop.set()
    assert run(settings, stop) == 0
