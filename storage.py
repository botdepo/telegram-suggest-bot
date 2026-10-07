"""Хранилище в SQLite: настройки бота и предложенные посты.

На botdepo писать на диск можно только в /data — эта папка переживает
перезапуски и передеплои. Остальная файловая система только для чтения.
Локально (где /data нет) база ляжет в ./data рядом с кодом.
"""
import os
import sqlite3
from pathlib import Path

DATA_DIR = Path(os.getenv("DATA_DIR") or ("/data" if Path("/data").is_dir() else "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

_db = sqlite3.connect(DATA_DIR / "suggest.db")
_db.executescript(
    """
    CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS posts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        message_id INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """
)
_db.commit()


def get(key: str) -> str | None:
    row = _db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def put(key: str, value: str) -> None:
    _db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    _db.commit()


def claim_owner(user_id: int) -> bool:
    """Первый, кто написал /start, становится владельцем. True — если это он."""
    if get("owner_id") is None:
        put("owner_id", str(user_id))
    return get("owner_id") == str(user_id)


def owner_id() -> int | None:
    value = get("owner_id")
    return int(value) if value else None


def add_post(user_id: int, message_id: int) -> int:
    cur = _db.execute("INSERT INTO posts (user_id, message_id) VALUES (?, ?)", (user_id, message_id))
    _db.commit()
    return cur.lastrowid


def get_post(post_id: int) -> tuple[int, int, str] | None:
    """(user_id, message_id, status) или None."""
    return _db.execute("SELECT user_id, message_id, status FROM posts WHERE id = ?", (post_id,)).fetchone()


def set_status(post_id: int, status: str) -> bool:
    """Меняет статус только у ждущего поста — чтобы двойное нажатие не опубликовало дважды."""
    cur = _db.execute("UPDATE posts SET status = ? WHERE id = ? AND status = 'pending'", (status, post_id))
    _db.commit()
    return cur.rowcount == 1


def pending_count(user_id: int | None = None) -> int:
    if user_id is None:
        return _db.execute("SELECT COUNT(*) FROM posts WHERE status = 'pending'").fetchone()[0]
    return _db.execute(
        "SELECT COUNT(*) FROM posts WHERE status = 'pending' AND user_id = ?", (user_id,)
    ).fetchone()[0]
