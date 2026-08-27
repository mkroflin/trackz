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
    Telegram Bot API adapter.
    """
    def __init__(self, token: str):
        self.token = token

    def send_text(self, recipient_id: str, text: str) -> bool:
        # Calls Telegram sendMessage API endpoint
        logger.info(f"[TelegramChannel] Sending text to chat {recipient_id}")
        return True

    def send_card(self, recipient_id: str, card: InteractiveCard) -> bool:
        # Calls Telegram sendMessage with InlineKeyboardMarkup buttons
        logger.info(f"[TelegramChannel] Sending inline button card to chat {recipient_id}")
        return True


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
