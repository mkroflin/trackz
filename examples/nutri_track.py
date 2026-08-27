"""
NutriTrack - Meal & Macro Tracking Application built on top of Trackz
"""

from typing import List, Optional
from pydantic import BaseModel, Field
from fastapi import FastAPI, HTTPException
import uvicorn

from trackz import DomainConfig, TrackzApp
from trackz.adapters.storage import SQLiteStorage
from trackz.adapters.channels import MockChannelAdapter


# -----------------------------------------------------------------------------
# 1. Pydantic Schemas for Meal & Calorie/Macro Extraction
# -----------------------------------------------------------------------------

class FoodItem(BaseModel):
    datum: str = Field(description="Date formatted as D.M.YYYY. (e.g. '27.8.2026.')")
    obrok: str = Field(description="Meal type: 'Doručak', 'Ručak', 'Večera', 'Užina', 'Suplementi'")
    hrana: str = Field(description="Food item or ingredient name (e.g. 'Pileća prsa', 'Riža', 'Maslinovo ulje')")
    kolicina: str = Field(description="Portion size or weight (e.g. '200g', '1 medium apple', '2 eggs')")
    kalorije: float = Field(description="Estimated energy in kcal (e.g. 330.0)")
    proteini_g: float = Field(description="Estimated protein content in grams (e.g. 42.5)")
    ugljikohidrati_g: float = Field(description="Estimated carbohydrates in grams (e.g. 50.0)")
    masti_g: float = Field(description="Estimated fat content in grams (e.g. 12.0)")
    napomena: Optional[str] = Field(default="", description="Optional context (e.g. 'Grilled', 'Sugar-free')")


class ParsedMealResponse(BaseModel):
    sažetak_obroka: str = Field(description="Short overall summary of the meal (e.g. 'Grilled chicken breast with brown rice and salad')")
    items: List[FoodItem] = Field(description="List of extracted ingredient items")
    ukupno_kalorija: float = Field(description="Total meal energy in kcal")
    ukupno_proteina: float = Field(description="Total meal protein in grams")
    ukupno_ugljikohidrata: float = Field(description="Total meal carbohydrates in grams")
    ukupno_masti: float = Field(description="Total meal fats in grams")
    savjet_nutricionista: Optional[str] = Field(default=None, description="Optional brief dietary insight or recommendation")


# -----------------------------------------------------------------------------
# 2. System Instruction & Domain Config
# -----------------------------------------------------------------------------

NUTRI_SYSTEM_PROMPT = """
You are an expert clinical nutritionist and dietary tracking assistant.
Analyze the user's meal description, recipe text, or meal photo and accurately extract:
1. Individual food items and portion weights/sizes.
2. Estimated energy (Calories in kcal).
3. Macronutrients: Protein (g), Carbohydrates (g), and Fat (g).

RULES:
- Always format dates as D.M.YYYY.
- If portion size is not specified, infer standard serving sizes (e.g. 1 egg ~ 50g, 1 banana ~ 120g).
- Provide accurate nutritional values based on standard USDA/EuroFIR database metrics.
- Return structured output matching ParsedMealResponse.
"""

NUTRI_QUERY_PROMPT = """
You are a personal dietary & nutrition analytics assistant.
You have access to the user's logged meals database.
Calculate daily caloric totals, macronutrient splits (P/C/F), answer questions about specific foods eaten, and evaluate adherence to dietary goals.
"""

nutri_domain = DomainConfig[FoodItem, ParsedMealResponse](
    name="NutriTrack",
    item_model=FoodItem,
    response_model=ParsedMealResponse,
    system_instruction=NUTRI_SYSTEM_PROMPT,
    query_instruction=NUTRI_QUERY_PROMPT
)


# -----------------------------------------------------------------------------
# 3. Trackz App Setup
# -----------------------------------------------------------------------------

trackz_app = TrackzApp(domain=nutri_domain)
trackz_app.use_storage(SQLiteStorage("nutritrack.db"))
mock_channel = trackz_app.register_channel("mock", MockChannelAdapter())


# -----------------------------------------------------------------------------
# 4. FastAPI REST Server
# -----------------------------------------------------------------------------

web_app = FastAPI(title="NutriTrack REST API", description="AI Meal & Macro Tracking Server powered by Trackz")


class ProcessTextRequest(BaseModel):
    user_id: str = "default_user"
    text: str


class QueryRequest(BaseModel):
    user_id: str = "default_user"
    question: str


@web_app.get("/")
def read_root():
    return {"app": "NutriTrack", "status": "running", "engine": "Trackz SDK"}


@web_app.post("/api/meal/process-text")
def process_meal_text(req: ProcessTextRequest):
    """
    Ingest a text meal description (e.g. '2 poached eggs, 2 slices whole wheat toast, 1/2 avocado, black coffee').
    Generates preview card with calorie and macro breakdown.
    """
    card = trackz_app.process_input(
        user_id=req.user_id,
        input_data=req.text,
        channel_name="mock"
    )
    return {
        "card_id": card.card_id,
        "title": card.title,
        "body_text": card.body_text,
        "raw_parsed": card.raw_data
    }


@web_app.post("/api/meal/confirm")
def confirm_meal(user_id: str = "default_user", card_id: Optional[str] = None):
    """
    Confirms and saves extracted food items to the database.
    """
    success = trackz_app.confirm_pending(user_id=user_id, card_id=card_id)
    if not success:
        raise HTTPException(status_code=400, detail="No active pending meal proposal found to confirm.")
    return {"status": "success", "message": "Meal logged successfully!"}


@web_app.post("/api/meal/query")
def query_nutrition(req: QueryRequest):
    """
    Natural language Q&A over logged nutrition history (e.g. 'How much protein did I consume today?').
    """
    answer = trackz_app.answer_query(user_id=req.user_id, question=req.question)
    return {"question": req.question, "answer": answer}


if __name__ == "__main__":
    uvicorn.run(web_app, host="0.0.0.0", port=8000)
