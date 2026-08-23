import sqlite3
from contextlib import contextmanager
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "doma_data.db")


def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS wallets (
            wallet_address TEXT PRIMARY KEY,
            points REAL,
            rank INTEGER,
            previous_day_rank INTEGER,
            referral_count INTEGER,
            total_entries INTEGER,
            week_number INTEGER,
            season_points REAL,
            weekly_points REAL,
            level TEXT,
            updated_at TEXT
        )
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS sync_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            synced_at TEXT,
            total_wallets INTEGER,
            total_season_points REAL,
            status TEXT,
            error TEXT
        )
        """
    )
    # lightweight history table so the frontend can chart total points over time
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS points_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recorded_at TEXT,
            total_wallets INTEGER,
            total_season_points REAL,
            total_weekly_points REAL
        )
        """
    )
    conn.commit()
    conn.close()


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()
