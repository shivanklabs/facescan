import threading
from unittest.mock import MagicMock, patch

import pytest
from app.core.services_setup import ContextConfigError
from app.core.settings import Settings
from app.worker import main, run


def test_worker_idle_loop_runs_and_stops(settings: Settings) -> None:
    assert run(settings, threading.Event(), max_iterations=3) == 3


def test_worker_stops_when_event_set(settings: Settings) -> None:
    stop = threading.Event()
    stop.set()
    assert run(settings, stop) == 0


def test_worker_cleanup_runs_at_most_once_an_hour(settings: Settings) -> None:
    """
    Purpose: verify cleanup_expired_attempts runs at most once per hour during idle polling.
    Inputs: settings.
    Outputs: None.
    Side effects: verifies cleanup_expired_attempts mock call count across 5 idle iterations.
    """
    mock_ctx = MagicMock()
    mock_ctx.queue.claim.return_value = None  # idle

    # Use 0 second sleep for fast test execution
    test_settings = settings.model_copy(update={"worker_poll_interval_seconds": 0})

    # A small monotonic clock (machine just booted) must still allow the first cleanup.
    with (
        patch("app.worker.time.monotonic", return_value=5.0),
        patch("app.worker.cleanup_expired_attempts") as mock_cleanup,
    ):
        iterations = run(test_settings, threading.Event(), max_iterations=5, ctx=mock_ctx)
        assert iterations == 5
        # Even across 5 idle polls, cleanup runs exactly once
        assert mock_cleanup.call_count == 1


def test_worker_fails_fast_in_prod_when_context_config_fails() -> None:
    """
    Purpose: verify that worker main() raises ContextConfigError in production.
    Inputs: none.
    Outputs: none.
    Side effects: tests main() exception behavior.
    """
    with (
        patch("app.worker.get_settings") as mock_settings,
        patch("app.worker.build_context", side_effect=ContextConfigError("missing key")),
    ):
        mock_settings.return_value = Settings(env="prod", log_level="INFO")
        with pytest.raises(ContextConfigError, match="missing key"):
            main()
