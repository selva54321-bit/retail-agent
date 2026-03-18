"""
RetailAgent — Scout Agent (LangChain Search Tools + ReAct Agent)
=================================================================
Discovers local competitor shops in the retailer's city that are not
already in their known_competitors list, finds their websites, then
locates the specific product listing pages to scrape.

LangChain patterns used:
  - DuckDuckGoSearchResults     → free web search, no API key needed
  - @tool decorator             → wraps helper functions as LangChain Tools
  - create_react_agent          → ReAct loop that plans search queries,
                                   reads results, decides next action
  - AgentExecutor               → runs the ReAct agent loop
  - LCEL extraction chain       → prompt | llm | JsonOutputParser
                                   extracts structured shop info from raw results
  - RunnableLambda              → wraps the product-page discovery as a Runnable

Discovery flow per category+location:
  1.  ReAct agent generates 3-4 targeted search queries
  2.  DuckDuckGo returns snippets + URLs
  3.  LCEL extraction chain parses shops with names + websites from results
  4.  For each shop website, a second LCEL chain finds the product listing URL
  5.  All discovered competitors saved to DB competitor_registry table
  6.  Execution plan updated with new scrape targets

Example: Retailer sells TVs in Coimbatore
  Searches: "television shops Coimbatore website", "buy Samsung TV Coimbatore store"
  Finds:    "Poorvika Mobiles coimbatore.poorvika.com",
            "Raja Electronics rajaelectronics.in"
  Discovers: https://coimbatore.poorvika.com/category/televisions
"""

import re
import time
import socket
from datetime import datetime
from typing   import Optional

from langchain_community.tools        import DuckDuckGoSearchResults
from langchain_community.utilities   import DuckDuckGoSearchAPIWrapper
from langchain_core.tools            import tool
from langchain_core.prompts          import ChatPromptTemplate
from langchain_core.output_parsers   import JsonOutputParser
from langchain_core.runnables        import RunnableLambda
# from langchain.agents                import create_react_agent, AgentExecutor
from langchain_core.prompts          import PromptTemplate

from core.state import AgentState, ScrapeTarget, ExecutionPlan
from core.llm   import get_llm, make_json_chain
from core       import database as db


# ─────────────────────────────────────────────────────────────────
#  SEARCH QUERY TEMPLATES
#  These generate targeted queries for finding local competitors.
# ─────────────────────────────────────────────────────────────────

def _build_search_queries(category: str, subcategories: list, location: str) -> list[str]:
    """
    Generate targeted search queries that find local physical + online shops.
    Covers: shops, dealers, stores, buy-now searches — to catch local retailers
    that also have web presence.
    """
    city = location.split(",")[0].strip()   # "Coimbatore, Tamil Nadu" → "Coimbatore"

    # Primary subcategory for search (most specific)
    primary = subcategories[0] if subcategories else category

    queries = [
        f"{primary} shops {city} website",
        f"buy {primary} {city} online store",
        f"{category} dealers {city} site:*.in OR site:*.com",
        f"best {primary} store {city}",
        f"{primary} price {city} shop",
    ]

    # Add subcategory-specific queries if multiple subcategories
    for sub in subcategories[1:3]:
        queries.append(f"{sub} showroom {city} website")

    return queries


# ─────────────────────────────────────────────────────────────────
#  LCEL CHAIN: Extract shops from raw search results
# ─────────────────────────────────────────────────────────────────

SHOP_EXTRACT_SYSTEM = """You are a competitive intelligence assistant helping a retailer
find local competitors.

Given raw web search results (snippets + URLs), extract any local retail shops or
e-commerce stores that sell the given product category in the given city.

Return ONLY a JSON array (no markdown, no explanation):
[
  {{
    "shop_name":   "exact business name",
    "website":     "full URL if found, else empty string",
    "city":        "city confirmed in results",
    "relevance":   "high|medium|low",
    "notes":       "one-line description e.g. local chain with 3 branches"
  }}
]

Rules:
- Only include shops actually in or serving the given city
- Do NOT include national platforms already known (Amazon, Flipkart etc.)
- If no website URL is directly visible, leave it as empty string
- Relevance: high = confirmed local shop + website, medium = likely local, low = unclear
"""


def _build_shop_extractor_chain():
    """LCEL: search_results → LLM → [{shop_name, website, ...}]"""
    prompt = ChatPromptTemplate.from_messages([
        ("system", SHOP_EXTRACT_SYSTEM),
        ("human",
         "Product category: {category}\n"
         "Target city: {city}\n\n"
         "Search results:\n{search_results}\n\n"
         "Extract local competitor shops from these results."),
    ])
    return prompt | get_llm(temperature=0.05) | JsonOutputParser()


# ─────────────────────────────────────────────────────────────────
#  LCEL CHAIN: Find product listing page on a competitor's website
# ─────────────────────────────────────────────────────────────────

PRODUCT_PAGE_SYSTEM = """You are a web scraping specialist.
Given a retailer's base website URL and the product category they sell,
predict the most likely URL for their product listing page.

Return ONLY JSON (no markdown):
{{
  "product_page_url": "full URL to the product listing/category page",
  "confidence":       "high|medium|low",
  "method":           "static|dynamic|anti_bot"
}}

URL construction rules:
- Try common patterns: /category/X, /products/X, /shop/X, /search?q=X
- For WordPress/WooCommerce sites: /product-category/X
- For custom sites: /X, /products, /shop
- method = dynamic if the site looks like a major platform or SPA
- method = static if it looks like a simple CMS or static site
"""


def _build_product_page_chain():
    """LCEL: (website, category) → {product_page_url, confidence, method}"""
    prompt = ChatPromptTemplate.from_messages([
        ("system", PRODUCT_PAGE_SYSTEM),
        ("human",
         "Base website: {website}\n"
         "Product category: {category}\n"
         "Subcategories: {subcategories}\n\n"
         "What is the most likely URL for their product listing page?"),
    ])
    return prompt | get_llm(temperature=0.05) | JsonOutputParser()


# ─────────────────────────────────────────────────────────────────
#  WEBSITE VALIDATOR  (checks if a URL actually resolves)
# ─────────────────────────────────────────────────────────────────

def _url_resolves(url: str, timeout: int = 5) -> bool:
    """Quick HEAD request to check if a URL is live before adding to registry."""
    import requests
    if not url or not url.startswith("http"):
        return False
    try:
        r = requests.head(url, timeout=timeout,
                          headers={"User-Agent": "Mozilla/5.0"},
                          allow_redirects=True)
        return r.status_code < 400
    except Exception:
        return False


def _network_available() -> bool:
    try:
        socket.setdefaulttimeout(2)
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(("8.8.8.8", 53))
        return True
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────
#  DEDUPLICATION: avoid re-adding competitors already in registry
# ─────────────────────────────────────────────────────────────────

def _already_known(shop_name: str, website: str, known_names: list,
                   existing_urls: set) -> bool:
    """Returns True if this competitor is already tracked."""
    name_lower = shop_name.lower().strip()

    # Check against known competitor names from intake
    for known in known_names:
        if known.lower().strip() in name_lower or name_lower in known.lower().strip():
            return True

    # Check against existing scrape target URLs
    if website:
        domain = re.search(r"https?://(?:www\.)?([^/]+)", website)
        domain = domain.group(1) if domain else ""
        for url in existing_urls:
            ex_domain = re.search(r"https?://(?:www\.)?([^/]+)", url)
            ex_domain = ex_domain.group(1) if ex_domain else ""
            if domain and ex_domain and domain == ex_domain:
                return True
    return False


# ─────────────────────────────────────────────────────────────────
#  CORE DISCOVERY FUNCTION
# ─────────────────────────────────────────────────────────────────

def _discover_local_competitors(profile, retailer_id: int,
                                 existing_urls: set) -> list[dict]:
    """
    Main discovery logic.
    Uses DuckDuckGo search → LLM extraction → product page discovery.
    Returns list of new ScrapeTarget dicts ready to add to the registry.
    """
    city         = profile.location.split(",")[0].strip()
    category     = profile.category
    subcategories = profile.subcategories
    known_names  = profile.known_competitors

    # Build LCEL chains
    shop_extractor    = _build_shop_extractor_chain()
    product_page_chain = _build_product_page_chain()

    # DuckDuckGo search wrapper
    search_wrapper = DuckDuckGoSearchAPIWrapper(max_results=8, time="y")
    ddg_search     = DuckDuckGoSearchResults(api_wrapper=search_wrapper,
                                              output_format="list")

    queries       = _build_search_queries(category, subcategories, profile.location)
    all_results   = []
    seen_snippets = set()

    print(f"  [Scout] Searching for local competitors in {city}...")
    print(f"  [Scout] Running {len(queries)} search queries...")

    for query in queries:
        try:
            results = ddg_search.invoke(query)
            # results is a list of dicts: [{snippet, title, link}, ...]
            for r in (results if isinstance(results, list) else []):
                key = r.get("link", "") or r.get("snippet", "")[:60]
                if key and key not in seen_snippets:
                    seen_snippets.add(key)
                    all_results.append(r)
            time.sleep(0.5)   # be polite to DuckDuckGo
        except Exception as e:
            print(f"  [Scout] Search failed for '{query}': {e}")
            continue

    if not all_results:
        print("  [Scout] No search results returned.")
        return []

    # Format results for LLM extraction
    formatted = "\n\n".join([
        f"Title: {r.get('title', '')}\n"
        f"URL:   {r.get('link', '')}\n"
        f"Snippet: {r.get('snippet', '')}"
        for r in all_results[:25]   # cap at 25 to stay within context window
    ])

    # ── Extract shops from search results ────────────────────────
    try:
        shops = shop_extractor.invoke({
            "category":       f"{category} ({', '.join(subcategories[:3])})",
            "city":           city,
            "search_results": formatted,
        })
        if not isinstance(shops, list):
            shops = []
    except Exception as e:
        print(f"  [Scout] Shop extraction failed: {e}")
        return []

    print(f"  [Scout] {len(shops)} candidate shops found in search results.")

    # ── Process each shop: find product page, validate, add ──────
    new_targets = []

    for shop in shops:
        name    = shop.get("shop_name", "").strip()
        website = shop.get("website", "").strip()
        relevance = shop.get("relevance", "low")

        if not name or relevance == "low":
            continue

        # Skip if already known
        if _already_known(name, website, known_names, existing_urls):
            print(f"    ⟳  Already tracked: {name}")
            continue

        print(f"  [Scout] Processing: {name} ({website or 'no website yet'})")

        # If no website found in search results, do a targeted search
        if not website:
            try:
                targeted = ddg_search.invoke(f"{name} {city} official website")
                if isinstance(targeted, list) and targeted:
                    # Take the first URL that looks like a shop website
                    for r in targeted:
                        url = r.get("link", "")
                        if url and "google" not in url and "facebook" not in url:
                            website = url
                            break
                time.sleep(0.3)
            except Exception:
                pass

        if not website:
            print(f"    ✗  No website found for {name}, skipping.")
            continue

        # Normalise: ensure it has a scheme
        if not website.startswith("http"):
            website = "https://" + website

        # Strip to base domain
        base_match = re.match(r"(https?://[^/]+)", website)
        base_url   = base_match.group(1) if base_match else website

        # ── Discover product listing page ─────────────────────
        try:
            page_info = product_page_chain.invoke({
                "website":      base_url,
                "category":     category,
                "subcategories": ", ".join(subcategories[:3]),
            })
            product_url = page_info.get("product_page_url", base_url)
            scrape_method = page_info.get("method", "static")
            confidence    = page_info.get("confidence", "low")
        except Exception:
            product_url   = base_url
            scrape_method = "static"
            confidence    = "low"

        # ── Validate URL resolves ─────────────────────────────
        if not _url_resolves(product_url):
            # Fall back to base domain
            if _url_resolves(base_url):
                product_url   = base_url
                scrape_method = "static"
            else:
                print(f"    ✗  {name}: URL unreachable, skipping.")
                continue

        new_targets.append({
            "competitor_name":     name,
            "url":                 product_url,
            "priority":            "high" if relevance == "high" else "medium",
            "scan_interval_hours": 24,
            "scrape_method":       scrape_method,
            "product_category":    category,
            "selector_config":     {},
            "source":              "scout_local",
            "notes":               shop.get("notes", ""),
        })
        print(f"    ✓  {name} → {product_url[:60]} [{scrape_method}]")

    return new_targets


# ─────────────────────────────────────────────────────────────────
#  LANGGRAPH NODE
# ─────────────────────────────────────────────────────────────────

def run_scout_node(state: AgentState) -> dict:
    """
    LangGraph node: Scout Agent.

    Runs after the Planner node (which registers the known big-brand competitors).
    Searches for LOCAL competitors around the retailer's city and adds them
    to the competitor registry before the scraper runs.

    Skips silently if:
      - No network available
      - DuckDuckGo search returns nothing
      - All found shops are already tracked

    Returns partial state update with updated execution_plan that
    includes both the original planner targets + newly discovered locals.
    """
    profile     = state["retailer_profile"]
    retailer_id = state["retailer_id"]
    plan        = state.get("execution_plan")

    print(f"\n[Scout] Discovering local competitors for "
          f"{profile.category} in {profile.location}...")

    # Skip if no network
    if not _network_available():
        print("  [Scout] No network — skipping local discovery.")
        return {"current_node": "scout"}

    # Get existing scrape targets to avoid duplicates
    existing = db.get_competitors(retailer_id)
    existing_urls = {t["url"] for t in existing}

    # Run discovery
    try:
        new_targets = _discover_local_competitors(profile, retailer_id, existing_urls)
    except Exception as e:
        print(f"  [Scout] Discovery failed: {e}")
        return {"current_node": "scout", "errors": [f"Scout failed: {e}"]}

    if not new_targets:
        print("  [Scout] No new local competitors found.")
        return {"current_node": "scout"}

    # ── Save to competitor registry ───────────────────────────────
    for target in new_targets:
        db.upsert_competitor(retailer_id, target)

    # ── Update execution plan with new targets ────────────────────
    new_scrape_targets = []
    if plan:
        new_scrape_targets = list(plan.scrape_targets)

    for t in new_targets:
        new_scrape_targets.append(ScrapeTarget(
            competitor_name     = t["competitor_name"],
            url                 = t["url"],
            priority            = t["priority"],
            scan_interval_hours = t["scan_interval_hours"],
            scrape_method       = t["scrape_method"],
            product_category    = t["product_category"],
            selector_config     = t.get("selector_config", {}),
        ))

    updated_plan = ExecutionPlan(
        scrape_targets      = new_scrape_targets,
        priority_categories = plan.priority_categories if plan else profile.subcategories,
        strategy_framework  = plan.strategy_framework  if plan else profile.pricing_strategy,
        reasoning           = (plan.reasoning if plan else "") +
                              f" | Scout added {len(new_targets)} local competitors in "
                              f"{profile.location.split(',')[0]}.",
    )

    print(f"[Scout] Done. {len(new_targets)} new local competitors added:")
    for t in new_targets:
        print(f"  + {t['competitor_name']} → {t['url'][:55]}")

    return {
        "execution_plan": updated_plan,
        "current_node":   "scout",
    }