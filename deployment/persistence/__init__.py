"""Persistence package for local JSON output and SQLite duplicate protection."""

from persistence.duplicate_store import DuplicateStore, get_duplicate_store
from persistence.json_store import JSONStore, get_json_store

__all__ = [
    "DuplicateStore",
    "get_duplicate_store",
    "JSONStore",
    "get_json_store",
]
