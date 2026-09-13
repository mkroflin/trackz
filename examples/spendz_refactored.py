"""
Spendz - Refactored Expense Tracker Application built on top of Trackz
"""

from typing import List, Optional
from pydantic import BaseModel, Field
from trackz import DomainConfig, TrackzApp
from trackz.adapters import SQLiteStorage, MockChannelAdapter


class ExpenseRow(BaseModel):
    date: str = Field(description="Date formatted strictly as D.M.YYYY.")
    item: str = Field(description="Item name (e.g. 'Cottage cheese', 'Fuel', 'Coffee')")
    quantity: str = Field(default="", description="Item quantity or weight if specified")
    store: str = Field(default="", description="Store/merchant name")
    category: str = Field(description="Expense category")
    amount: float = Field(description="Amount in EUR")
    exclude: bool = Field(default=False, description="Exclude from totals")
    comment: str = Field(default="", description="Optional context or location")
    person: Optional[str] = Field(default=None, description="Payer person name")


class ParsedExpenseResponse(BaseModel):
    merchant_summary: str = Field(description="Summary of transaction")
    items: List[ExpenseRow] = Field(description="Extracted expense items")
    suggested_new_category: Optional[str] = Field(default=None, description="Suggested missing category")
    extracted_person: Optional[str] = Field(default=None, description="Payer name")


SPENDZ_SYSTEM_PROMPT = """
You are an intelligent financial assistant extracting line items from receipts or text messages.
Classify items into categories and output ParsedExpenseResponse.
"""

expense_domain = DomainConfig[ExpenseRow, ParsedExpenseResponse](
    name="Spendz",
    item_model=ExpenseRow,
    response_model=ParsedExpenseResponse,
    system_instruction=SPENDZ_SYSTEM_PROMPT
)

app = TrackzApp(domain=expense_domain)
app.use_storage(SQLiteStorage("spendz.db"))
app.register_channel("mock", MockChannelAdapter())

if __name__ == "__main__":
    print("Spendz Trackz application configured successfully!")
