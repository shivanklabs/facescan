"""Logging setup with a redaction filter (security invariant 7).

The filter masks email addresses and bearer/JWT-looking tokens in log messages.
TODO: extend to OTPs, signed URLs and any structured `extra` fields; add tests in
tests/security/test_no_biometrics_in_logs.py.
"""

import logging
import re

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[email]"),
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer [token]"),
    (re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"), "[jwt]"),
)


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


def configure_logging(level: str) -> None:
    """
    Purpose: configure root logging once.
    Inputs: level name. Outputs: none.
    Side effects: replaces root handlers with one redacting stream handler.
    """
    handler = logging.StreamHandler()
    handler.addFilter(RedactionFilter())
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
