import logging
import uuid
from typing import Type, List, Dict, Any, Optional, Callable, Generic, TypeVar, Protocol
from pydantic import BaseModel, Field

logger = logging.getLogger("trackz")

T_Item = TypeVar("T_Item", bound=BaseModel)
T_Response = TypeVar("T_Response", bound=BaseModel)


class CardButton(BaseModel):
    id: str
    title: str
    action: str = "confirm"  # e.g., "confirm", "discard", "add_taxonomy", "edit"
    payload: Optional[Dict[str, Any]] = None


class InteractiveCard(BaseModel):
    card_id: str = Field(default_factory=lambda: f"card_{uuid.uuid4().hex[:8]}")
    title: str
    body_text: str
    buttons: List[CardButton] = Field(default_factory=list)
    raw_data: Optional[Any] = None


class TaxonomyProvider(Protocol):
    def get_taxonomy(self, user_id: str) -> List[str]:
        ...


class StorageAdapter(Protocol[T_Item]):
    def save_items(self, user_id: str, items: List[T_Item]) -> bool:
        ...

    def fetch_items(self, user_id: str, filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        ...

    def fetch_taxonomy(self, user_id: str) -> List[str]:
        ...


class ChannelAdapter(Protocol):
    def send_text(self, recipient_id: str, text: str) -> bool:
        ...

    def send_card(self, recipient_id: str, card: InteractiveCard) -> bool:
        ...


class DomainConfig(Generic[T_Item, T_Response]):
    """
    Declarative specification of a tracking domain (e.g. NutriTrack, Spendz, FitTrack).
    """
    def __init__(
        self,
        name: str,
        item_model: Type[T_Item],
        response_model: Type[T_Response],
        system_instruction: str,
        query_instruction: Optional[str] = None,
        extract_items_fn: Optional[Callable[[T_Response], List[T_Item]]] = None,
        format_card_fn: Optional[Callable[[T_Response, str], InteractiveCard]] = None,
    ):
        self.name = name
        self.item_model = item_model
        self.response_model = response_model
        self.system_instruction = system_instruction
        self.query_instruction = query_instruction or (
            f"You are an AI assistant for {name}. Answer the user's question accurately based strictly on their tracking history."
        )
        self.extract_items_fn = extract_items_fn or (
            lambda resp: getattr(resp, "items", [])
        )
        self.format_card_fn = format_card_fn or self._default_format_card

    def _default_format_card(self, response: T_Response, card_id: str) -> InteractiveCard:
        items = self.extract_items_fn(response)
        summary = (
            getattr(response, "merchant_summary", None)
            or getattr(response, "meal_summary", None)
            or f"{self.name} Log"
        )
        
        lines = [f"📋 *{summary}* ({len(items)} items):\n"]
        for idx, item in enumerate(items, 1):
            item_dict = item.model_dump() if hasattr(item, "model_dump") else dict(item)
            lines.append(f"{idx}. {item_dict}")
            
        body = "\n".join(lines)
        return InteractiveCard(
            card_id=card_id,
            title=summary,
            body_text=body,
            buttons=[
                CardButton(id="btn_confirm", title="✅ Confirm & Save", action="confirm"),
                CardButton(id="btn_discard", title="❌ Discard", action="discard")
            ],
            raw_data=response
        )


class TrackzApp(Generic[T_Item, T_Response]):
    """
    Main orchestrator for a Trackz application instance.
    """
    def __init__(
        self,
        domain: DomainConfig[T_Item, T_Response],
        gemini_api_key: Optional[str] = None,
        gemini_model: str = "gemini-2.5-flash"
    ):
        from trackz.parser import GeminiStructuredParser
        from trackz.state import StateEngine
        from trackz.query import NLQueryEngine

        self.domain = domain
        self.parser = GeminiStructuredParser(
            api_key=gemini_api_key,
            model_name=gemini_model
        )
        self.state_engine = StateEngine()
        self.query_engine = NLQueryEngine(parser=self.parser)
        self.storage: Optional[StorageAdapter[T_Item]] = None
        self.channels: Dict[str, ChannelAdapter] = {}

    def use_storage(self, storage: StorageAdapter[T_Item]) -> "TrackzApp[T_Item, T_Response]":
        self.storage = storage
        return self

    def register_channel(self, name: str, channel: ChannelAdapter) -> ChannelAdapter:
        self.channels[name] = channel
        return channel

    def process_input(
        self,
        user_id: str,
        input_data: Any,
        mime_type: Optional[str] = None,
        channel_name: Optional[str] = None
    ) -> InteractiveCard:
        """
        Ingests user input (text, photo, audio), fetches live taxonomy, calls Gemini structured parser,
        creates a pending session state card, and broadcasts to channel.
        """
        taxonomy = []
        if self.storage and hasattr(self.storage, "fetch_taxonomy"):
            taxonomy = self.storage.fetch_taxonomy(user_id)

        parsed_response = self.parser.parse(
            domain=self.domain,
            input_data=input_data,
            mime_type=mime_type,
            taxonomy=taxonomy
        )

        card_id = self.state_engine.save_pending(user_id, parsed_response)
        card = self.domain.format_card_fn(parsed_response, card_id)

        if channel_name and channel_name in self.channels:
            self.channels[channel_name].send_card(user_id, card)

        return card

    def confirm_pending(self, user_id: str, card_id: Optional[str] = None) -> bool:
        """
        Confirms a pending state card and commits extracted item rows to storage.
        """
        pending = self.state_engine.get_pending(user_id, card_id)
        if not pending:
            logger.warning(f"No pending transaction found for user {user_id}")
            return False

        items = self.domain.extract_items_fn(pending)
        if self.storage:
            success = self.storage.save_items(user_id, items)
            if success:
                self.state_engine.clear_pending(user_id, card_id)
                return True
        return False

    def answer_query(self, user_id: str, question: str) -> str:
        """
        Answers natural language queries using historical data from storage.
        """
        history = []
        if self.storage:
            history = self.storage.fetch_items(user_id)

        return self.query_engine.answer_query(
            domain=self.domain,
            user_id=user_id,
            question=question,
            history_rows=history
        )
