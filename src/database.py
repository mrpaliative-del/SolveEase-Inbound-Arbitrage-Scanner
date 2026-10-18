
import os
import sqlite3
from datetime import datetime, timezone


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _connect(db_path: str) -> sqlite3.Connection:
    if db_path != ":memory:":
        directory = os.path.dirname(os.path.abspath(db_path))
        os.makedirs(directory, exist_ok=True)

    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def initialize_database(db_path: str = "data/inbound_arbitrage.db") -> None:
    with _connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_usage (
                usage_date TEXT PRIMARY KEY,
                used INTEGER NOT NULL DEFAULT 0 CHECK (used >= 0),
                reserved INTEGER NOT NULL DEFAULT 0 CHECK (reserved >= 0)
            )
        """)


def reserve_daily_slot(
    db_path: str,
    daily_limit: int,
) -> bool:
    """Atomically reserve capacity for one processing request."""
    if daily_limit < 1:
        return False

    day = _today()

    with _connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT OR IGNORE INTO daily_usage (usage_date) VALUES (?)",
            (day,),
        )
        cursor = conn.execute("""
            UPDATE daily_usage
            SET reserved = reserved + 1
            WHERE usage_date = ?
              AND used + reserved < ?
        """, (day, daily_limit))

        return cursor.rowcount == 1


def complete_daily_slot(db_path: str) -> None:
    """Convert one reservation into a successful request."""
    day = _today()

    with _connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT reserved FROM daily_usage WHERE usage_date = ?",
            (day,),
        ).fetchone()

        if row is None or row["reserved"] < 1:
            raise RuntimeError("No daily usage reservation exists.")

        conn.execute("""
            UPDATE daily_usage
            SET reserved = reserved - 1, used = used + 1
            WHERE usage_date = ?
        """, (day,))


def release_daily_slot(db_path: str) -> None:
    """Release a reservation when processing fails."""
    day = _today()

    with _connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("""
            UPDATE daily_usage
            SET reserved = reserved - 1
            WHERE usage_date = ? AND reserved > 0
        """, (day,))


def get_daily_usage(db_path: str) -> dict:
    day = _today()

    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT used, reserved FROM daily_usage WHERE usage_date = ?",
            (day,),
        ).fetchone()

    if row is None:
        return {"used": 0, "reserved": 0}

    return {"used": row["used"], "reserved": row["reserved"]}
