"""Pytest configuration for the backend test suite."""

import os

os.environ.setdefault("ENABLE_VECTOR_STACK", "false")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://postgres:postgres@localhost:5432/sukilo_test",
)
