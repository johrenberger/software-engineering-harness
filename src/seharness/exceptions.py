"""Domain exceptions for the harness."""

from __future__ import annotations


class HarnessError(Exception):
    """Base class for harness errors."""


class ConfigurationError(HarnessError):
    """Raised when configuration cannot be loaded or is invalid."""
