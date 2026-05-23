"""Prepare the local SQLite database for cloud import."""
import sqlite3
from pathlib import Path

root = Path(__file__).resolve().parent.parent
db_path = root / "data" / "uriage.db"

if not db_path.exists():
    raise SystemExit(f"database not found: {db_path}")

with sqlite3.connect(str(db_path)) as conn:
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    table_count = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
    ).fetchone()[0]

print("OK: SQLite database is ready for Turso import.")
print(f"DB: {db_path}")
print(f"Tables: {table_count}")
