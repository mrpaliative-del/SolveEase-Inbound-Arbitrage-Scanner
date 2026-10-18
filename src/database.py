
import sqlite3
from typing import Optional, Tuple


def initialize_database(
    db_path: str = "inbound_arbitrage.db",
) -> sqlite3.Connection:
    """Initialize the 4G relational data layer."""

    conn = sqlite3.connect(
        db_path,
        timeout=15,
        isolation_level="DEFERRED",
    )
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 15000")

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            email TEXT UNIQUE,
            tier TEXT NOT NULL DEFAULT 'FREE'
                CHECK (tier IN ('FREE', 'PREMIUM', 'BUSINESS')),
            monthly_allowance INTEGER NOT NULL DEFAULT 15
                CHECK (monthly_allowance >= 0),
            usage_count INTEGER NOT NULL DEFAULT 0
                CHECK (usage_count >= 0),
            usage_month TEXT NOT NULL DEFAULT
                (strftime('%Y-%m', 'now'))
        );

        CREATE TABLE IF NOT EXISTS transaction_queue (
            task_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            pattern_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'QUEUED'
                CHECK (status IN (
                    'QUEUED', 'PROCESSING', 'COMPLETED',
                    'FAILED', 'CANCELLED'
                )),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id)
                REFERENCES users(user_id)
                ON DELETE RESTRICT
        );

        CREATE INDEX IF NOT EXISTS idx_queue_status_created
            ON transaction_queue(status, created_at);

        CREATE INDEX IF NOT EXISTS idx_queue_user
            ON transaction_queue(user_id);
    """)

    conn.commit()
    return conn


def _current_month() -> str:
    """Return the current UTC calendar month."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m")


def _rollover_month(
    conn: sqlite3.Connection,
    user_id: str,
) -> None:
    """Reset usage when a new UTC calendar month begins."""
    conn.execute(
        """
        UPDATE users
        SET usage_count = 0, usage_month = ?
        WHERE user_id = ? AND usage_month <> ?
        """,
        (_current_month(), user_id, _current_month()),
    )


def verify_user_quota(
    conn: sqlite3.Connection,
    user_id: str,
) -> Optional[Tuple[str, int, int]]:
    """
    Return (tier, usage_count, monthly_allowance).
    This is an informational check, not a usage reservation.
    """
    try:
        with conn:
            _rollover_month(conn, user_id)
            return conn.execute(
                """
                SELECT tier, usage_count, monthly_allowance
                FROM users
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
    except sqlite3.Error:
        raise


def reserve_user_usage(
    conn: sqlite3.Connection,
    user_id: str,
) -> bool:
    """
    Atomically reserve one unit of monthly quota.
    Returns False for unknown users or exhausted allowances.
    """
    month = _current_month()

    try:
        with conn:
            conn.execute(
                """
                UPDATE users
                SET usage_count = 0, usage_month = ?
                WHERE user_id = ? AND usage_month <> ?
                """,
                (month, user_id, month),
            )

            cursor = conn.execute(
                """
                UPDATE users
                SET usage_count = usage_count + 1
                WHERE user_id = ?
                  AND usage_count < monthly_allowance
                """,
                (user_id,),
            )

            return cursor.rowcount == 1

    except sqlite3.Error:
        raise


def increment_user_usage(
    conn: sqlite3.Connection,
    user_id: str,
) -> bool:
    """
    Backwards-compatible usage function.
    Returns True only when quota was successfully reserved.
    """
    return reserve_user_usage(conn, user_id)


def create_user(
    conn: sqlite3.Connection,
    user_id: str,
    email: Optional[str] = None,
    tier: str = "FREE",
    monthly_allowance: int = 15,
) -> None:
    """Create a user explicitly; never seed production accounts."""
    if tier not in {"FREE", "PREMIUM", "BUSINESS"}:
        raise ValueError("Unsupported user tier.")

    if monthly_allowance < 0:
        raise ValueError("Monthly allowance cannot be negative.")

    with conn:
        conn.execute(
            """
            INSERT INTO users (
                user_id, email, tier, monthly_allowance
            )
            VALUES (?, ?, ?, ?)
            """,
            (user_id, email, tier, monthly_allowance),
        )


def enqueue_task(
    conn: sqlite3.Connection,
    task_id: str,
    user_id: str,
    pattern_id: str,
) -> None:
    """Add a task to the queue after quota is reserved."""
    with conn:
        conn.execute(
            """
            INSERT INTO transaction_queue (
                task_id, user_id, pattern_id
            )
            VALUES (?, ?, ?)
            """,
            (task_id, user_id, pattern_id),
        )
