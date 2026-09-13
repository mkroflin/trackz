import json
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

    def send_card(self, recipient_id: str, card: InteractiveCard) -> Any:
        ...

    def edit_card(
        self,
        recipient_id: str,
        message_id: Any,
        text: str,
        buttons: Optional[List[Any]] = None
    ) -> bool:
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
        heuristic_matcher: Optional[Callable[[str, Dict[str, Any]], Optional[T_Response]]] = None,
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
        self.heuristic_matcher = heuristic_matcher

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
        gemini_model: str = "gemini-2.5-flash",
        vision_model: Optional[str] = None,
        text_model: Optional[str] = None,
        heuristic_matcher: Optional[Callable[[str, Dict[str, Any]], Optional[T_Response]]] = None,
    ):
        from trackz.parser import GeminiStructuredParser
        from trackz.state import StateEngine
        from trackz.query import NLQueryEngine

        self.domain = domain
        self.heuristic_matcher = heuristic_matcher or domain.heuristic_matcher
        self.parser = GeminiStructuredParser(
            api_key=gemini_api_key,
            model_name=gemini_model,
            vision_model=vision_model,
            text_model=text_model,
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
        channel_name: Optional[str] = None,
        model_override: Optional[str] = None,
        skip_heuristic: bool = False,
    ) -> InteractiveCard:
        """
        Ingests user input (text, photo, audio), checks heuristic pre-matching for text
        (saving LLM costs), calls Gemini structured parser if needed, creates a pending session card,
        and broadcasts to channel.
        """
        taxonomy = []
        if self.storage and hasattr(self.storage, "fetch_taxonomy"):
            taxonomy = self.storage.fetch_taxonomy(user_id)

        parsed_response = None
        matched_by = "gemini"

        # 1. Check heuristic matcher for text inputs
        if isinstance(input_data, str) and not skip_heuristic and self.heuristic_matcher:
            history = self.storage.fetch_items(user_id) if self.storage else []
            context = {
                "user_id": user_id,
                "taxonomy": taxonomy,
                "history": history,
            }
            try:
                heuristic_match = self.heuristic_matcher(input_data, context)
                if heuristic_match is not None:
                    parsed_response = heuristic_match
                    matched_by = "heuristic"
                    logger.info(json.dumps({
                        "event": "trackz_heuristic_match",
                        "domain": self.domain.name,
                        "user_id": user_id,
                        "input": input_data
                    }))
            except Exception as e:
                logger.warning(f"[TrackzApp] Heuristic matcher error: {e}. Falling back to LLM parser.")

        # 2. Fall back to LLM Structured Parser
        if parsed_response is None:
            parsed_response = self.parser.parse(
                domain=self.domain,
                input_data=input_data,
                mime_type=mime_type,
                taxonomy=taxonomy,
                model=model_override,
            )

        # 3. Store pending session state
        card_id = self.state_engine.save_pending(
            user_id=user_id,
            parsed_data=parsed_response,
            metadata={"matched_by": matched_by}
        )
        card = self.domain.format_card_fn(parsed_response, card_id)

        # 4. Dispatch to channel and record message ID
        if channel_name and channel_name in self.channels:
            msg_id = self.channels[channel_name].send_card(user_id, card)
            if msg_id and msg_id is not True:
                self.state_engine.set_message_id(user_id, card_id, msg_id)

        return card

    def confirm_pending(
        self,
        user_id: str,
        card_id: Optional[str] = None,
        channel_name: Optional[str] = None,
        confirmation_text: Optional[str] = None
    ) -> bool:
        """
        Confirms a pending state card, commits extracted item rows to storage,
        and optionally updates the channel card in-place.
        """
        record = self.state_engine.get_pending_record(user_id, card_id)
        if not record or not record.get("data"):
            logger.warning(f"No pending transaction found for user {user_id}")
            return False

        pending = record["data"]
        items = self.domain.extract_items_fn(pending)
        if self.storage:
            success = self.storage.save_items(user_id, items)
            if success:
                actual_card_id = record.get("card_id", card_id)
                msg_id = record.get("message_id")
                if channel_name and channel_name in self.channels and msg_id:
                    channel = self.channels[channel_name]
                    if hasattr(channel, "edit_card"):
                        text = confirmation_text or f"✅ *Saved {len(items)} item(s) to storage!*"
                        channel.edit_card(user_id, msg_id, text, buttons=[])

                self.state_engine.clear_pending(user_id, actual_card_id)
                return True
        return False

    def discard_pending(
        self,
        user_id: str,
        card_id: Optional[str] = None,
        channel_name: Optional[str] = None,
        discard_text: Optional[str] = None
    ) -> bool:
        """
        Discards a pending card and optionally updates the channel message in-place.
        """
        record = self.state_engine.get_pending_record(user_id, card_id)
        if not record:
            return False

        actual_card_id = record.get("card_id", card_id)
        msg_id = record.get("message_id")
        if channel_name and channel_name in self.channels and msg_id:
            channel = self.channels[channel_name]
            if hasattr(channel, "edit_card"):
                text = discard_text or "❌ *Entry discarded.*"
                channel.edit_card(user_id, msg_id, text, buttons=[])

        self.state_engine.clear_pending(user_id, actual_card_id)
        return True

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
