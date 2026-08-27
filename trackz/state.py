import time
import uuid
import logging
from typing import Dict, Optional, Any

logger = logging.getLogger("trackz.state")


class StateEngine:
    """
    Session state engine for caching pending card previews with TTL expiration.
    """
    def __init__(self, ttl_seconds: int = 3600):
        self._store: Dict[str, Dict[str, Any]] = {}
        self.ttl_seconds = ttl_seconds

    def save_pending(
        self,
        user_id: str,
        parsed_data: Any,
        card_id: Optional[str] = None
    ) -> str:
        cid = card_id or f"card_{uuid.uuid4().hex[:8]}"
        key = f"{user_id}:{cid}"
        self._store[key] = {
            "card_id": cid,
            "user_id": user_id,
            "data": parsed_data,
            "created_at": time.time()
        }
        # Also maintain latest pending key for fast single-user confirmation
        self._store[f"latest:{user_id}"] = key
        logger.info(f"Saved pending state {cid} for user {user_id}")
        return cid

    def get_pending(self, user_id: str, card_id: Optional[str] = None) -> Optional[Any]:
        if card_id:
            key = f"{user_id}:{card_id}"
        else:
            key = self._store.get(f"latest:{user_id}")

        if not key or key not in self._store:
            return None

        record = self._store[key]
        if time.time() - record["created_at"] > self.ttl_seconds:
            logger.info(f"Pending state {key} expired.")
            self.clear_pending(user_id, card_id)
            return None

        return record["data"]

    def clear_pending(self, user_id: str, card_id: Optional[str] = None):
        if card_id:
            key = f"{user_id}:{card_id}"
            if key in self._store:
                del self._store[key]
        else:
            latest_key = self._store.get(f"latest:{user_id}")
            if latest_key and latest_key in self._store:
                del self._store[latest_key]
            if f"latest:{user_id}" in self._store:
                del self._store[f"latest:{user_id}"]
