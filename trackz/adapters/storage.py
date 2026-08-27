import json
import sqlite3
import logging
from typing import List, Dict, Any, Optional, TypeVar
from pydantic import BaseModel

logger = logging.getLogger("trackz.adapters.storage")


class InMemoryStorage:
    """
    In-memory storage adapter for testing and rapid prototyping.
    """
    def __init__(self):
        self._items: Dict[str, List[Dict[str, Any]]] = {}
        self._taxonomy: Dict[str, List[str]] = {}

    def save_items(self, user_id: str, items: List[Any]) -> bool:
        if user_id not in self._items:
            self._items[user_id] = []
        for item in items:
            record = item.model_dump() if isinstance(item, BaseModel) else dict(item)
            self._items[user_id].append(record)
        logger.info(f"[InMemoryStorage] Saved {len(items)} items for user {user_id}")
        return True

    def fetch_items(self, user_id: str, filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        return self._items.get(user_id, [])

    def fetch_taxonomy(self, user_id: str) -> List[str]:
        return self._taxonomy.get(user_id, [])

    def set_taxonomy(self, user_id: str, taxonomy: List[str]):
        self._taxonomy[user_id] = taxonomy


class SQLiteStorage:
    """
    SQLite persistence adapter.
    """
    def __init__(self, db_path: str = "trackz.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tracking_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_taxonomy (
                    user_id TEXT NOT NULL,
                    tag TEXT NOT NULL,
                    PRIMARY KEY (user_id, tag)
                );
            """)
            conn.commit()

    def save_items(self, user_id: str, items: List[Any]) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            for item in items:
                record = item.model_dump() if isinstance(item, BaseModel) else dict(item)
                conn.execute(
                    "INSERT INTO tracking_records (user_id, payload_json) VALUES (?, ?)",
                    (user_id, json.dumps(record, ensure_ascii=False))
                )
            conn.commit()
        logger.info(f"[SQLiteStorage] Saved {len(items)} items to {self.db_path} for {user_id}")
        return True

    def fetch_items(self, user_id: str, filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        rows = []
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT payload_json FROM tracking_records WHERE user_id = ? ORDER BY id DESC",
                (user_id,)
            )
            for row in cursor.fetchall():
                rows.append(json.loads(row[0]))
        return rows

    def fetch_taxonomy(self, user_id: str) -> List[str]:
        tags = []
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT tag FROM user_taxonomy WHERE user_id = ?", (user_id,))
            for row in cursor.fetchall():
                tags.append(row[0])
        return tags
