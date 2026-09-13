import json
import logging
import os
import time
from typing import Any, List, Optional, Union
from google import genai
from google.genai import types
from pydantic import BaseModel

from trackz.core import DomainConfig, T_Item, T_Response

logger = logging.getLogger("trackz.parser")


class GeminiStructuredParser:
    """
    Multimodal Gemini parser enforcing Pydantic structured output.
    Supports dynamic model selection (e.g. lightweight models for cost optimization).
    """
    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: str = "gemini-2.5-flash",
        vision_model: Optional[str] = None,
        text_model: Optional[str] = None,
    ):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        self.model_name = model_name
        self.vision_model = vision_model or model_name
        self.text_model = text_model or model_name
        self._client: Optional[genai.Client] = None

    def _get_client(self, override_key: Optional[str] = None) -> genai.Client:
        key = (override_key or self.api_key or os.environ.get("GEMINI_API_KEY") or "").strip()
        if not key:
            raise ValueError(
                "Gemini API Key is missing. Pass api_key or set GEMINI_API_KEY environment variable."
            )
        if not self._client or override_key:
            self._client = genai.Client(api_key=key)
        return self._client

    def parse(
        self,
        domain: DomainConfig[T_Item, T_Response],
        input_data: Union[str, bytes, List[Any]],
        mime_type: Optional[str] = None,
        taxonomy: Optional[List[str]] = None,
        override_api_key: Optional[str] = None,
        model: Optional[str] = None,
    ) -> T_Response:
        client = self._get_client(override_key=override_api_key)
        start_time = time.perf_counter()

        formatted_contents = []
        is_media = False
        if isinstance(input_data, list):
            formatted_contents = input_data
            is_media = any(isinstance(x, types.Part) or isinstance(x, bytes) for x in input_data)
        elif isinstance(input_data, bytes) and mime_type:
            formatted_contents = [types.Part.from_bytes(data=input_data, mime_type=mime_type)]
            is_media = True
        else:
            formatted_contents = [input_data]

        selected_model = model or (self.vision_model if is_media else self.text_model) or self.model_name

        system_instruction = domain.system_instruction
        if taxonomy:
            taxonomy_str = ", ".join([f"'{t}'" for t in taxonomy])
            system_instruction += f"\n\nALLOWED TAXONOMY / CATEGORIES:\nStrictly prefer categories/tags from this list: [{taxonomy_str}]."

        try:
            response = client.models.generate_content(
                model=selected_model,
                contents=formatted_contents,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    response_mime_type="application/json",
                    response_schema=domain.response_model,
                    temperature=0.1,
                ),
            )
            latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
            logger.info(json.dumps({
                "event": "trackz_parse_success",
                "domain": domain.name,
                "latency_ms": latency_ms,
                "model": selected_model
            }))

            if hasattr(response, "parsed") and response.parsed:
                return response.parsed
            
            # Fallback if parsed object is raw JSON text
            if response.text:
                return domain.response_model.model_validate_json(response.text)

            raise ValueError("Gemini returned empty response text.")

        except Exception as e:
            latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
            logger.error(json.dumps({
                "event": "trackz_parse_error",
                "domain": domain.name,
                "latency_ms": latency_ms,
                "error": str(e)
            }))
            raise
