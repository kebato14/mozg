"""
Реестр документов — SQLite
"""

import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "registry.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                doc_number  TEXT UNIQUE NOT NULL,
                filename    TEXT NOT NULL,
                drive_id    TEXT NOT NULL,
                drive_url   TEXT NOT NULL,
                user_id     INTEGER NOT NULL,
                username    TEXT,
                full_name   TEXT,
                uploaded_at TEXT NOT NULL
            )
        """)
        conn.commit()


def next_doc_number() -> str:
    year = datetime.now().year
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM documents WHERE doc_number LIKE ?",
            (f"УЦЦП-{year}-%",)
        ).fetchone()
        num = (row["cnt"] or 0) + 1
    return f"УЦЦП-{year}-{num:04d}"


def save_document(doc_number, filename, drive_id, drive_url, user_id, username, full_name) -> int:
    uploaded_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO documents (doc_number, filename, drive_id, drive_url, user_id, username, full_name, uploaded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (doc_number, filename, drive_id, drive_url, user_id, username, full_name, uploaded_at))
        conn.commit()
        return cur.lastrowid


def search_by_number(query: str) -> list:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM documents WHERE doc_number LIKE ? ORDER BY uploaded_at DESC LIMIT 10",
            (f"%{query}%",)
        ).fetchall()
    return [dict(r) for r in rows]


def search_by_date(date_str: str) -> list:
    if "." in date_str:
        parts = date_str.split(".")
        if len(parts) == 3:
            date_str = f"{parts[2]}-{parts[1]}-{parts[0]}"
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM documents WHERE uploaded_at LIKE ? ORDER BY uploaded_at DESC LIMIT 10",
            (f"{date_str}%",)
        ).fetchall()
    return [dict(r) for r in rows]


def search_by_user(query: str) -> list:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM documents WHERE username LIKE ? OR full_name LIKE ? ORDER BY uploaded_at DESC LIMIT 10",
            (f"%{query}%", f"%{query}%")
        ).fetchall()
    return [dict(r) for r in rows]


def get_all() -> list:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM documents ORDER BY uploaded_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_recent(limit=10) -> list:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM documents ORDER BY uploaded_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_stats() -> dict:
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) as c FROM documents").fetchone()["c"]
        today = conn.execute(
            "SELECT COUNT(*) as c FROM documents WHERE uploaded_at LIKE ?",
            (f"{datetime.now().strftime('%Y-%m-%d')}%",)
        ).fetchone()["c"]
    return {"total": total, "today": today}


init_db()
