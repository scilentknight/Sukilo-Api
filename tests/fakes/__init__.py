"""Provide reusable test-only application adapters."""

from tests.fakes.iam import InMemoryCountryReader, RecordingAuditWriter

__all__ = ["InMemoryCountryReader", "RecordingAuditWriter"]
