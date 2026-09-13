# 🚀 Trackz Framework

**Trackz** is a generic, lightweight Python framework for building multimodal, AI-powered tracking applications (e.g., **NutriTrack** for meal photo & macro tracking, **Spendz** for receipt & expense tracking, **FitTrack** for workout logging, etc.).

---

## 🌟 Core Architecture

`Trackz` decouples tracking applications into 6 pluggable abstractions:

1. **`DomainConfig`**: Declarative Pydantic schemas (`ItemModel`, `ResponseModel`) and system prompt definitions.
2. **`GeminiStructuredParser`**: Multimodal LLM parser leveraging Google Gemini 2.5 / 3.5 Flash for guaranteed JSON output.
3. **`StateEngine`**: TTL session cache for human-in-the-loop interactive preview cards (`Confirm`, `Edit`, `Discard`).
4. **`ChannelAdapter`**: Standardized messaging protocol for Telegram, WhatsApp, FastAPI REST endpoints, Slack, CLI.
5. **`StorageAdapter`**: Storage abstraction supporting SQLite, Google Sheets, PostgreSQL, Supabase, or Memory.
6. **`NLQueryEngine`**: Natural language Q&A engine over historical tracking records.

---

## 🥗 Example: Building `NutriTrack` (Meal & Macro Tracker)

```python
from pydantic import BaseModel, Field
from typing import List, Optional
from trackz import DomainConfig, TrackzApp
from trackz.adapters import SQLiteStorage, MockChannelAdapter

# 1. Define Domain Schema
class FoodItem(BaseModel):
    date: str = Field(description="Date formatted as D.M.YYYY.")
    meal: str = Field(description="Meal type (e.g. Lunch)")
    food: str = Field(description="Food item name")
    quantity: str = Field(description="Portion size/weight")
    calories: float = Field(description="Calories in kcal")
    protein_g: float = Field(description="Protein in grams")
    carbs_g: float = Field(description="Carbs in grams")
    fat_g: float = Field(description="Fats in grams")

class ParsedMealResponse(BaseModel):
    meal_summary: str
    items: List[FoodItem]
    total_calories: float
    total_protein: float

# 2. Instantiate App
domain = DomainConfig(
    name="NutriTrack",
    item_model=FoodItem,
    response_model=ParsedMealResponse,
    system_instruction="Extract food items, portions, calories, and macros from meal photos/text."
)

app = TrackzApp(domain=domain)
app.use_storage(SQLiteStorage("nutrition.db"))
app.register_channel("default", MockChannelAdapter())

# 3. Process Input & Confirm
card = app.process_input("user_123", "2 poached eggs, 2 slices toast, half avocado")
print(card.body_text)

# Confirm and write to DB
app.confirm_pending("user_123", card.card_id)

# 4. Query Analytics
answer = app.answer_query("user_123", "How much protein did I consume today?")
print(answer)
```

---

## 📁 Repository Structure

```text
trackz/
├── trackz/
│   ├── core.py              # DomainConfig, TrackzApp, InteractiveCard
│   ├── parser.py            # GeminiStructuredParser with Pydantic support
│   ├── state.py             # StateEngine (TTL Session Store)
│   ├── query.py             # NLQueryEngine (Natural language analytics)
│   └── adapters/
│       ├── channels.py      # Telegram, WhatsApp, Mock adapters
│       └── storage.py       # SQLite, InMemory adapters
├── examples/
│   ├── nutri_track.py       # Complete NutriTrack Meal/Macro application & FastAPI REST server
│   └── spendz_refactored.py # Spendz refactored using Trackz
├── requirements.txt
└── README.md
```
