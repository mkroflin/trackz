import json
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional
from google.genai import types

from trackz.core import DomainConfig, T_Item, T_Response

logger = logging.getLogger("trackz.query")


class NLQueryEngine:
    """
    RAG & Natural Language Query engine for historical tracking logs.
    """
    def __init__(self, parser: Any):
        self.parser = parser

    def answer_query(
        self,
        domain: DomainConfig[T_Item, T_Response],
        user_id: str,
        question: str,
        history_rows: List[Dict[str, Any]],
        override_api_key: Optional[str] = None
    ) -> str:
        client = self.parser._get_client(override_key=override_api_key)
        start_time = time.perf_counter()
        today_str = datetime.now().strftime("%d.%m.%Y.")

        clean_question = question.strip()
        if clean_question.startswith("?") or clean_question.startswith("/q"):
            clean_question = clean_question.lstrip("?").replace("/query", "").replace("/q", "").strip()

        system_instruction = f"""
{domain.query_instruction}

SYSTEM CONTEXT:
- Today's date: {today_str}
- Current active user: "{user_id}"

QUERY RULES:
1. Filter historical records accurately by user context and date scope (e.g. today, this week, this month).
2. Perform precise calculations (e.g., total calories/macros, total expense sums, streak counts).
3. Respond in friendly, helpful, natural language with clear Markdown formatting.
"""

        rows_json = json.dumps(history_rows, ensure_ascii=False, indent=2)
        prompt = f"User Question: {clean_question}\n\nHistorical Tracking Data ({len(history_rows)} records):\n{rows_json}"

        try:
            response = client.models.generate_content(
                model=self.parser.model_name,
                contents=[prompt],
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=0.2,
                ),
            )
            latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
            logger.info(json.dumps({
                "event": "trackz_query_success",
                "domain": domain.name,
                "latency_ms": latency_ms,
                "history_count": len(history_rows)
            }))
            return response.text.strip() if response.text else "No data found for your query."
        except Exception as e:
            latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
            logger.error(json.dumps({
                "event": "trackz_query_error",
                "domain": domain.name,
                "latency_ms": latency_ms,
                "error": str(e)
            }))
            return f"❌ Error querying tracking history: {str(e)}"
