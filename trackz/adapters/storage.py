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


def sanitize_cell_text(text: Any) -> str:
    """Escapes leading formula trigger characters (=, +, -, @) to prevent CSV/Formula injection in Google Sheets."""
    if text is None:
        return ""
    clean = str(text).strip()
    if clean and clean[0] in ['=', '+', '-', '@', '\t', '\r']:
        return "'" + clean
    return clean


class GoogleSheetsStorage:
    """
    Google Sheets persistence adapter.
    Appends tracking records as formatted rows and fetches historical logs/taxonomies.
    """
    SCOPES = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.file",
    ]

    def __init__(
        self,
        spreadsheet_id: str,
        sheet_name: str = "Sheet1",
        credentials_path: Optional[str] = None,
        service_account_json: Optional[Any] = None,
        taxonomy_range: Optional[str] = None,
        item_to_row_fn: Optional[Any] = None,
        cache_ttl_seconds: float = 300.0,
    ):
        self.spreadsheet_id = spreadsheet_id
        self.sheet_name = sheet_name
        self.credentials_path = credentials_path
        self.service_account_json = service_account_json
        self.taxonomy_range = taxonomy_range
        self.item_to_row_fn = item_to_row_fn
        self.cache_ttl_seconds = cache_ttl_seconds

        self._service = None
        self._taxonomy_cache: List[str] = []
        self._taxonomy_cache_time: float = 0.0
        self._items_cache: List[Dict[str, Any]] = []
        self._items_cache_time: float = 0.0

    def _get_service(self):
        if self._service:
            return self._service

        creds = None
        # 1. Try raw JSON (string or dict)
        if self.service_account_json:
            try:
                from google.oauth2.service_account import Credentials
                info = (
                    json.loads(self.service_account_json)
                    if isinstance(self.service_account_json, str)
                    else self.service_account_json
                )
                creds = Credentials.from_service_account_info(info, scopes=self.SCOPES)
            except Exception as e:
                logger.error(f"[GoogleSheetsStorage] Failed to parse service_account_json: {e}")

        # 2. Try credentials file path
        if not creds and self.credentials_path:
            try:
                from google.oauth2.service_account import Credentials
                creds = Credentials.from_service_account_file(self.credentials_path, scopes=self.SCOPES)
            except Exception as e:
                logger.error(f"[GoogleSheetsStorage] Failed to load credentials file ({self.credentials_path}): {e}")

        # 3. Fall back to Application Default Credentials (ADC)
        if not creds:
            try:
                import google.auth
                creds, _ = google.auth.default(scopes=self.SCOPES)
                logger.info("[GoogleSheetsStorage] Using Google Application Default Credentials (ADC).")
            except Exception as e:
                logger.debug(f"[GoogleSheetsStorage] Application Default Credentials (ADC) failed: {e}")

        if not creds:
            raise ValueError(
                "No valid Google credentials provided. Specify credentials_path, "
                "service_account_json, or configure Application Default Credentials (ADC)."
            )

        from googleapiclient.discovery import build
        self._service = build("sheets", "v4", credentials=creds)
        return self._service

    def save_items(self, user_id: str, items: List[Any]) -> bool:
        service = self._get_service()
        if not service:
            logger.error("[GoogleSheetsStorage] Service unavailable. Cannot save items.")
            return False

        values: List[List[Any]] = []
        for item in items:
            if self.item_to_row_fn:
                row = self.item_to_row_fn(item, user_id)
            elif isinstance(item, BaseModel):
                row = [sanitize_cell_text(v) for v in item.model_dump().values()]
            elif isinstance(item, dict):
                row = [sanitize_cell_text(v) for v in item.values()]
            else:
                row = [sanitize_cell_text(str(item))]
            values.append(row)

        if not values:
            return True

        try:
            target_range = f"{self.sheet_name}!A:Z"
            service.spreadsheets().values().append(
                spreadsheetId=self.spreadsheet_id,
                range=target_range,
                valueInputOption="USER_ENTERED",
                body={"values": values},
            ).execute()
            logger.info(f"[GoogleSheetsStorage] Appended {len(values)} rows to {self.spreadsheet_id} ({self.sheet_name})")
            # Invalidate items cache so next read sees the fresh rows
            self._items_cache = []
            self._items_cache_time = 0.0
            return True
        except Exception as e:
            logger.error(f"[GoogleSheetsStorage] Error appending rows: {e}")
            return False

    def fetch_items(
        self,
        user_id: str,
        filters: Optional[Dict[str, Any]] = None,
        force_refresh: bool = False
    ) -> List[Dict[str, Any]]:
        import time
        now = time.time()
        if not force_refresh and self._items_cache and (now - self._items_cache_time < self.cache_ttl_seconds):
            return self._items_cache

        service = self._get_service()
        if not service:
            return self._items_cache

        try:
            target_range = f"{self.sheet_name}!A:Z"
            result = service.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_id,
                range=target_range
            ).execute()

            raw_rows = result.get("values", [])
            if not raw_rows or len(raw_rows) < 2:
                return []

            headers = [str(h).strip() for h in raw_rows[0]]
            parsed_rows = []
            for r in raw_rows[1:]:
                if not r or not any(str(cell).strip() for cell in r):
                    continue
                row_dict = {}
                for idx, col_name in enumerate(headers):
                    val = r[idx] if idx < len(r) else ""
                    row_dict[col_name] = val
                parsed_rows.append(row_dict)

            self._items_cache = parsed_rows
            self._items_cache_time = now
            return parsed_rows
        except Exception as e:
            logger.error(f"[GoogleSheetsStorage] Error fetching rows: {e}")
            return self._items_cache

    def fetch_taxonomy(self, user_id: str) -> List[str]:
        if not self.taxonomy_range:
            return []

        import time
        now = time.time()
        if self._taxonomy_cache and (now - self._taxonomy_cache_time < self.cache_ttl_seconds):
            return self._taxonomy_cache

        service = self._get_service()
        if not service:
            return self._taxonomy_cache

        try:
            result = service.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_id,
                range=self.taxonomy_range
            ).execute()

            rows = result.get("values", [])
            taxonomies = []
            for r in rows:
                if r and str(r[0]).strip():
                    val = str(r[0]).strip()
                    if val.lower() not in ["category", "kategorija", "taxonomy", "tag"]:
                        taxonomies.append(val)

            self._taxonomy_cache = taxonomies
            self._taxonomy_cache_time = now
            return taxonomies
        except Exception as e:
            logger.error(f"[GoogleSheetsStorage] Error fetching taxonomy: {e}")
            return self._taxonomy_cache

