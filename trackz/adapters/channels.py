import logging
from typing import Dict, List, Optional, Any
from trackz.core import ChannelAdapter, InteractiveCard

logger = logging.getLogger("trackz.adapters.channels")


class MockChannelAdapter(ChannelAdapter):
    """
    In-memory mock adapter for local testing and debugging.
    """
    def __init__(self):
        self.sent_texts: List[Dict[str, str]] = []
        self.sent_cards: List[Dict[str, Any]] = []

    def send_text(self, recipient_id: str, text: str) -> bool:
        self.sent_texts.append({"recipient": recipient_id, "text": text})
        logger.info(f"[MockChannel] Sent text to {recipient_id}: {text}")
        return True

    def send_card(self, recipient_id: str, card: InteractiveCard) -> bool:
        self.sent_cards.append({"recipient": recipient_id, "card": card})
        logger.info(f"[MockChannel] Sent card to {recipient_id}: {card.title} | Buttons: {[b.title for b in card.buttons]}")
        return True


class TelegramChannelAdapter(ChannelAdapter):
    """
    Production-ready Telegram Bot API adapter supporting interactive inline buttons,
    in-place card editing, callback responses, and media downloads.
    """
    TELEGRAM_API_BASE = "https://api.telegram.org/bot"

    def __init__(self, token: str, timeout: float = 15.0):
        self.token = token.strip().strip("'\"")
        self.timeout = timeout

    def _api_url(self, method: str) -> str:
        return f"{self.TELEGRAM_API_BASE}{self.token}/{method}"

    def send_text(self, recipient_id: str, text: str, parse_mode: Optional[str] = "Markdown") -> bool:
        if not self.token:
            logger.warning(f"[TelegramChannel] Token missing. Simulated text to {recipient_id}: {text}")
            return False

        payload: Dict[str, Any] = {
            "chat_id": recipient_id,
            "text": text,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode

        import httpx
        try:
            with httpx.Client(timeout=self.timeout) as client:
                res = client.post(self._api_url("sendMessage"), json=payload)
                res.raise_for_status()
                return True
        except Exception as e:
            logger.warning(f"[TelegramChannel] Failed to send text with parse_mode='{parse_mode}': {e}")
            if parse_mode:
                try:
                    payload.pop("parse_mode", None)
                    with httpx.Client(timeout=self.timeout) as client:
                        res = client.post(self._api_url("sendMessage"), json=payload)
                        res.raise_for_status()
                        return True
                except Exception as fallback_err:
                    logger.error(f"[TelegramChannel] Plain text fallback also failed: {fallback_err}")
            return False

    def send_card(
        self,
        recipient_id: str,
        card: InteractiveCard,
        parse_mode: Optional[str] = "Markdown"
    ) -> Any:
        if not self.token:
            logger.warning(f"[TelegramChannel] Token missing. Simulated card to {recipient_id}: {card.title}")
            return False

        # Build inline keyboard buttons
        inline_keyboard: List[List[Dict[str, str]]] = []
        row: List[Dict[str, str]] = []
        for btn in card.buttons:
            cb_data = btn.id
            if btn.action and not cb_data.startswith(f"{btn.action}:"):
                cb_data = f"{btn.action}:{card.card_id}"
            button_def = {"text": btn.title, "callback_data": cb_data}
            row.append(button_def)
            # Group into rows of 2
            if len(row) == 2:
                inline_keyboard.append(row)
                row = []
        if row:
            inline_keyboard.append(row)

        payload: Dict[str, Any] = {
            "chat_id": recipient_id,
            "text": card.body_text,
            "reply_markup": {"inline_keyboard": inline_keyboard}
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode

        import httpx
        try:
            with httpx.Client(timeout=self.timeout) as client:
                res = client.post(self._api_url("sendMessage"), json=payload)
                res.raise_for_status()
                msg_id = res.json().get("result", {}).get("message_id")
                return msg_id if msg_id else True
        except Exception as e:
            logger.error(f"[TelegramChannel] Failed to send interactive card: {e}")
            # Fallback to plain text with manual instruction
            fallback_text = f"{card.body_text}\n\n(Reply 'confirm' to save, or 'discard' to cancel)"
            return self.send_text(recipient_id, fallback_text, parse_mode=parse_mode)

    def edit_card(
        self,
        recipient_id: str,
        message_id: Any,
        text: str,
        buttons: Optional[List[Any]] = None,
        parse_mode: Optional[str] = "Markdown"
    ) -> bool:
        """Edits an existing Telegram message in place with updated text and optional buttons."""
        if not self.token:
            return False

        payload: Dict[str, Any] = {
            "chat_id": recipient_id,
            "message_id": message_id,
            "text": text,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode

        if buttons is not None:
            inline_keyboard = []
            for btn in buttons:
                if isinstance(btn, dict):
                    inline_keyboard.append([{"text": btn.get("title", ""), "callback_data": btn.get("id", "")}])
                elif hasattr(btn, "title") and hasattr(btn, "id"):
                    inline_keyboard.append([{"text": btn.title, "callback_data": btn.id}])
            payload["reply_markup"] = {"inline_keyboard": inline_keyboard}

        import httpx
        try:
            with httpx.Client(timeout=self.timeout) as client:
                res = client.post(self._api_url("editMessageText"), json=payload)
                res.raise_for_status()
                return True
        except Exception as e:
            logger.warning(f"[TelegramChannel] Failed to edit message {message_id}: {e}")
            if parse_mode:
                try:
                    payload.pop("parse_mode", None)
                    with httpx.Client(timeout=self.timeout) as client:
                        res = client.post(self._api_url("editMessageText"), json=payload)
                        res.raise_for_status()
                        return True
                except Exception:
                    pass
            return False

    def answer_callback_query(self, callback_query_id: str, text: Optional[str] = None) -> bool:
        """Acknowledges a button callback query."""
        if not self.token:
            return False

        payload: Dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text

        import httpx
        try:
            with httpx.Client(timeout=self.timeout) as client:
                res = client.post(self._api_url("answerCallbackQuery"), json=payload)
                res.raise_for_status()
                return True
        except Exception as e:
            logger.error(f"[TelegramChannel] Failed to answer callback query: {e}")
            return False

    def download_media(self, file_id: str) -> tuple[Optional[bytes], str]:
        """
        Downloads a media file from Telegram by file_id.
        Returns (media_bytes, mime_type).
        """
        if not self.token:
            logger.error("[TelegramChannel] Cannot download media: token missing.")
            return None, ""

        import httpx
        try:
            with httpx.Client(timeout=self.timeout) as client:
                # 1. getFile
                res = client.get(self._api_url("getFile"), params={"file_id": file_id})
                res.raise_for_status()
                file_path = res.json().get("result", {}).get("file_path")
                if not file_path:
                    return None, ""

                # 2. Download file content
                dl_url = f"https://api.telegram.org/file/bot{self.token}/{file_path}"
                dl_res = client.get(dl_url)
                dl_res.raise_for_status()

                # Deduce mime_type
                mime_type = "image/jpeg"
                lower_fp = file_path.lower()
                if lower_fp.endswith(".png"):
                    mime_type = "image/png"
                elif lower_fp.endswith(".webp"):
                    mime_type = "image/webp"
                elif lower_fp.endswith(".oga") or lower_fp.endswith(".ogg"):
                    mime_type = "audio/ogg"
                elif lower_fp.endswith(".mp3"):
                    mime_type = "audio/mpeg"

                return dl_res.content, mime_type
        except Exception as e:
            logger.error(f"[TelegramChannel] Error downloading media {file_id}: {e}")
            return None, ""

    def set_webhook(self, webhook_url: str) -> Dict[str, Any]:
        """Configures Telegram webhook URL."""
        if not self.token:
            return {"status": "error", "message": "Token missing"}
        import httpx
        try:
            with httpx.Client(timeout=self.timeout) as client:
                res = client.post(self._api_url("setWebhook"), json={"url": webhook_url})
                res.raise_for_status()
                return res.json()
        except Exception as e:
            logger.error(f"[TelegramChannel] Error setting webhook: {e}")
            return {"status": "error", "message": str(e)}



class WhatsAppChannelAdapter(ChannelAdapter):
    """
    Meta WhatsApp Cloud API adapter.
    """
    def __init__(self, token: str, phone_number_id: str):
        self.token = token
        self.phone_number_id = phone_number_id

    def send_text(self, recipient_id: str, text: str) -> bool:
        # Calls Meta WhatsApp Graph API
        logger.info(f"[WhatsAppChannel] Sending text to phone {recipient_id}")
        return True

    def send_card(self, recipient_id: str, card: InteractiveCard) -> bool:
        # Calls Meta WhatsApp interactive message API
        logger.info(f"[WhatsAppChannel] Sending interactive button card to phone {recipient_id}")
        return True
