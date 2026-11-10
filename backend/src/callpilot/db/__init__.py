"""SQLite storage: schema, connections and seed data."""

from callpilot.db.connection import connect, init_db

__all__ = ["connect", "init_db"]
