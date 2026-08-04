"""Small SQLite resource-lifetime helpers."""
from __future__ import annotations

from contextlib import contextmanager
import sqlite3
from typing import Iterator


@contextmanager
def connection(*args, row_factory=None, **kwargs) -> Iterator[sqlite3.Connection]:
    """Open a transactional SQLite connection and deterministically close it."""
    db = sqlite3.connect(*args, **kwargs)
    if row_factory is not None:
        db.row_factory = row_factory
    try:
        with db:
            yield db
    finally:
        db.close()
