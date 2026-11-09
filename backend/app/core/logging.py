"""Logging setup with a redaction filter (security invariant 7).

The filter masks email addresses, bearer/JWT-looking tokens and signed-URL signatures in log
messages, including uvicorn's access log.
TODO: extend to OTPs and any structured `extra` fields; add tests in
tests/security/test_no_biometrics_in_logs.py.
"""

import logging
import re

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[email]"),
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer [token]"),
    (re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"), "[jwt]"),
    (re.compile(r"(?i)(signature=)[^&\s\"']+"), r"\1[redacted]"),
)
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


def redact(text: str) -> str:
    """Purpose: mask sensitive substrings. Inputs: text. Outputs: redacted text. No side effects."""
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class RedactionFilter(logging.Filter):
    """Rewrites each record's message with sensitive substrings masked."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Purpose: redact record in place. Outputs: True (never drops). Side effects: mutates."""
        record.msg = redact(record.getMessage())
        record.args = None
        return True


class ArgsRedactionFilter(logging.Filter):
    """Redacts string args but keeps the args tuple (uvicorn's access formatter needs it)."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Purpose: redact msg and string args. Outputs: True. Side effects: mutates record."""
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
        return True


def configure_logging(level: str) -> None:
    """
    Purpose: configure root logging once.
    Inputs: level name. Outputs: none.
    Side effects: replaces root handlers with one redacting stream handler; adds a redaction
        filter to uvicorn's own handlers (they do not propagate to root).
    """
    handler = logging.StreamHandler()
    handler.addFilter(RedactionFilter())
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    for name in _UVICORN_LOGGERS:
        for uvicorn_handler in logging.getLogger(name).handlers:
            if not any(isinstance(f, ArgsRedactionFilter) for f in uvicorn_handler.filters):
                uvicorn_handler.addFilter(ArgsRedactionFilter())
