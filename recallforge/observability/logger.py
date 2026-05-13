from __future__ import annotations

import logging
import sys

_LOG_FORMAT = (
    "%(asctime)s | %(levelname)-8s | "
    "request_id=%(request_id)s tenant_id=%(tenant_id)s "
    "document_id=%(document_id)s job_id=%(job_id)s | "
    "%(name)s | %(message)s"
)

_DEFAULT_EXTRA = {
    "request_id": "-",
    "tenant_id": "-",
    "document_id": "-",
    "job_id": "-",
}


class _ExtraFilter(logging.Filter):
    """Ensure every log record has the structured context fields, defaulting to '-'."""

    def filter(self, record: logging.LogRecord) -> bool:
        for key, default in _DEFAULT_EXTRA.items():
            if not hasattr(record, key):
                setattr(record, key, default)
        return True


# Module-level singleton to avoid adding duplicate filter instances.
_extra_filter = _ExtraFilter()


def get_logger(name: str) -> logging.Logger:
    """Return a logger with RecallForge structured format.

    Usage::

        logger = get_logger(__name__)
        logger.info("document ingested", extra={"document_id": "42", "tenant_id": "acme"})
    """
    logger = logging.getLogger(name)

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(_LOG_FORMAT))
        handler.addFilter(_extra_filter)
        logger.addHandler(handler)

    return logger
