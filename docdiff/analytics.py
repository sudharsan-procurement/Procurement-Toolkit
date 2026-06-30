"""
Lightweight usage analytics — a daily access counter.

Records ONE row per (day, browser session) in a local SQLite file, plus the
signed-in identity when Google Sign-In is enabled. From that we can report:
    * daily session count   (browser/tab loads — anonymous + signed-in)
    * daily unique users    (distinct signed-in identities; needs sign-in)

Deliberately simple and dependency-free (sqlite3 is stdlib). Honest limits:
    * It counts what reaches THIS running instance. On an ephemeral host
      (e.g. a free cloud container that restarts) the file can be reset.
    * Without sign-in, "users" is unknowable, so we report sessions — a browser
      reload in a new tab is a new session. It is not a substitute for a real
      analytics product (GA / Plausible), just a built-in rough counter.
"""

from __future__ import annotations

import datetime
import sqlite3
from pathlib import Path

from .settings import CONFIG_DIR

DB_PATH: Path = CONFIG_DIR / "analytics.db"


def _conn() -> sqlite3.Connection:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS visits (
               day        TEXT NOT NULL,
               session_id TEXT NOT NULL,
               identity   TEXT,
               first_seen TEXT NOT NULL,
               PRIMARY KEY (day, session_id)
           )"""
    )
    return conn


def record_visit(session_id: str, identity: str | None = None) -> None:
    """Log one visit for today. Idempotent per (day, session_id).

    If the identity becomes known later in the same session (user signs in),
    a second call updates the stored identity without creating a new row.
    """
    if not session_id:
        return
    day = datetime.date.today().isoformat()
    now = datetime.datetime.now().isoformat(timespec="seconds")
    try:
        with _conn() as conn:
            conn.execute(
                """INSERT INTO visits (day, session_id, identity, first_seen)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(day, session_id) DO UPDATE SET
                       identity = COALESCE(excluded.identity, visits.identity)""",
                (day, session_id, identity, now),
            )
    except Exception:
        # Analytics must never break the app.
        pass


def daily_counts(days: int = 30) -> list[dict]:
    """Per-day [{day, sessions, users}] for the last `days` days (oldest first)."""
    since = (datetime.date.today() - datetime.timedelta(days=days - 1)).isoformat()
    try:
        with _conn() as conn:
            rows = conn.execute(
                """SELECT day,
                          COUNT(*)                AS sessions,
                          COUNT(DISTINCT identity) AS users
                   FROM visits
                   WHERE day >= ?
                   GROUP BY day
                   ORDER BY day""",
                (since,),
            ).fetchall()
    except Exception:
        return []
    # COUNT(DISTINCT identity) ignores NULLs, so `users` = distinct signed-in users.
    return [{"day": r[0], "sessions": r[1], "users": r[2]} for r in rows]


def counts_for(day: str | None = None) -> dict:
    """{sessions, users} for a single day (default: today)."""
    day = day or datetime.date.today().isoformat()
    try:
        with _conn() as conn:
            r = conn.execute(
                """SELECT COUNT(*), COUNT(DISTINCT identity)
                   FROM visits WHERE day = ?""",
                (day,),
            ).fetchone()
    except Exception:
        return {"sessions": 0, "users": 0}
    return {"sessions": r[0] or 0, "users": r[1] or 0}


def totals() -> dict:
    """All-time {sessions, users} (sessions counted across distinct day+session)."""
    try:
        with _conn() as conn:
            r = conn.execute(
                "SELECT COUNT(*), COUNT(DISTINCT identity) FROM visits"
            ).fetchone()
    except Exception:
        return {"sessions": 0, "users": 0}
    return {"sessions": r[0] or 0, "users": r[1] or 0}
