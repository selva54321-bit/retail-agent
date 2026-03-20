from core.state import AgentState, RetailerProfile
from agents.scout_agent import run_scout_node
from core.database import init_db
from core.llm import set_provider

set_provider("gemini") # User switched to gemini in llm.py recently
init_db()

profile = RetailerProfile(
    store_name="Test Electronics",
    category="Smart TV",
    subcategories=["OLED TV", "4K TV"],
    location="Saibaba Colony, Coimbatore, Tamil Nadu",
    known_competitors=["Amazon", "Flipkart"],
    pricing_strategy="price_match"
)

state = AgentState(
    retailer_id=0,
    retailer_profile=profile,
    execution_plan=None
)

print("Running isolated scout test...")
res = run_scout_node(state)

print("\n--- SCOUT RESULTS ---")
print(res)
