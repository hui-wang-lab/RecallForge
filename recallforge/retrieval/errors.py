"""Errors raised by the M4 retrieval pipeline."""

from __future__ import annotations


class RetrievalError(RuntimeError):
    """Base retrieval error."""


class QueryRejectedError(RetrievalError):
    """Raised when a query is rejected before retrieval."""


class FilterBuilderError(RetrievalError):
    """Raised when client filters contain forbidden or unknown keys."""


class RerankerError(RetrievalError):
    """Base reranker error."""


class RerankerConfigurationError(RerankerError):
    """Raised when reranker configuration is invalid."""


class RerankerProviderError(RerankerError):
    """Raised when the reranker provider call fails."""
