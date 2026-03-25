"""
RetailAgent — Scout Agent (DuckDuckGo City Search)
===================================================
Purpose:
  Search DuckDuckGo for electronics/category shops in the retailer's city.
  Extract real local store names from search results.
  Resolve each store name to a working website URL.
  Register them as scrape targets (one per catalog product).

Flow:
  1. Run 3 targeted DDG queries for shops in the retailer's city
     e.g. "electronics shops Coimbatore", "TV dealers Coimbatore website"
  2. LCEL chain: raw snippets → LLM → [{shop_name, website}]
  3. For each shop:
       a. Check planner's COMPETITOR_URL_MAP first (known chains)
       b. Else use the URL from search result directly
       c. Validate the URL actually resolves (HEAD request)
  4. Skip shops already registered by planner
  5. Register remaining shops as targets, one per catalog product

Max 5 new competitors per run to avoid scraper overload.
"""

import re
import time
import socket
from typing import Optional

import requests as _requests
from langchain_community.tools      import DuckDuckGoSearchResults
from langchain_community.utilities  import DuckDuckGoSearchAPIWrapper
from langchain_core.prompts         import ChatPromptTemplate
from langchain_core.output_parsers  import JsonOutputParser

from core.state import AgentState, ScrapeTarget, ExecutionPlan
from core.llm   import get_llm
from core       import database as db

# Import the planner's URL map so we can resolve known chain names to URLs
from agents.planner_agent import COMPETITOR_URL_MAP


# ─────────────────────────────────────────────────────────────────
#  CONSTANTS
# ─────────────────────────────────────────────────────────────────

MAX_NEW_COMPETITORS = 5   # cap per cycle to avoid scraper overload

# Domains to reject — directories, social media, maps, reviews
BLACKLIST_DOMAINS = {
    "justdial.com", "indiamart.com", "sulekha.com", "tradeindia.com",
    "yellowpages.in", "google.com", "facebook.com", "instagram.com",
    "twitter.com", "youtube.com", "wikipedia.org", "amazon.in",
    "flipkart.com", "snapdeal.com", "meesho.com",
    "maps.google", "magicbricks.com", "99acres.com", "quora.com",
    "reddit.com", "trustpilot.com", "mouthshut.com",
}


# ─────────────────────────────────────────────────────────────────
#  SEARCH QUERY BUILDER
# ─────────────────────────────────────────────────────────────────

def _build_queries(category: str, city: str) -> list[str]:
    """
    3 targeted queries that surface local retail shop websites.
    Explicit 'site' and 'website' keywords push DDG to return
    shop homepages rather than directory listings.
    """
    return [
        f"{category} shops in {city} website",
        f"buy {category} {city} local store",
        f"{category} dealers {city}",
    ]


# ─────────────────────────────────────────────────────────────────
#  LCEL EXTRACTION CHAIN
# ─────────────────────────────────────────────────────────────────

EXTRACT_SYSTEM = """You are a competitive intelligence assistant for an electronics retailer.

Given web search result snippets, identify which of these well-known electronics 
retail chains are present in or serve the given city:

Known electronics chains to look for:
- Poorvika Mobiles       → poorvika.com
- Sathya Agencies        → sathya.in
- Darling Electronics    → darling.in
- Girias                 → girias.com
- Vasanth and Co         → vasanthandco.com
- Reliance Digital       → reliancedigital.in
- Sangeetha Mobiles      → sangeetha.com
- Viveks                 → viveks.com
- Pai International      → pai.in
- Lot Mobiles            → lotmobiles.com

Return ONLY a JSON array of the chains you can confirm are in or serve the city
— no markdown, no explanation:
[
  {{"shop_name": "Poorvika Mobiles", "website": "https://www.poorvika.com"}},
  {{"shop_name": "Girias",           "website": "https://www.girias.com"}}
]

Rules:
- Only include chains from the list above that appear in the search results
- Do NOT invent chains not in the list above
- Do NOT include: Amazon, Flipkart, Snapdeal, Meesho — these are handled separately
- Do NOT include: JustDial, Sulekha, directories, social media
- If a chain is not mentioned in the snippets at all, do not include it
"""


def _build_extract_chain():
    prompt = ChatPromptTemplate.from_messages([
        ("system", EXTRACT_SYSTEM),
        ("human",
         "Category: {category}\n"
         "City: {city}\n\n"
         "Search results:\n{results}\n\n"
         "Extract local shops:"),
    ])
    return prompt | get_llm(temperature=0.0) | JsonOutputParser()


# ─────────────────────────────────────────────────────────────────
#  URL RESOLUTION
# ─────────────────────────────────────────────────────────────────

def _resolve_url(shop_name: str, raw_url: str) -> Optional[str]:
    """
    Get a working base URL for a shop.

    Priority:
      1. planner's COMPETITOR_URL_MAP  (known chains with verified URLs)
      2. raw_url from search result    (if it resolves)
      3. constructed www.{slug}.com    (last resort guess)

    Returns None if no working URL found.
    """
    name_lower = shop_name.lower().strip()

    # ── 1. Known chain lookup ──────────────────────────────────
    for key, (base_url, _method) in COMPETITOR_URL_MAP.items():
        if key in name_lower:
            return base_url

    # ── 2. URL from search result ──────────────────────────────
    if raw_url and raw_url.startswith("http"):
        # Strip to base domain
        m = re.match(r"(https?://(?:www\.)?[^/]+)", raw_url)
        base = m.group(1) if m else raw_url
        # Skip blacklisted domains
        domain = _get_domain(base)
        if not any(bl in domain for bl in BLACKLIST_DOMAINS):
            if _url_resolves(base):
                return base

    # ── 3. Guess from shop name ────────────────────────────────
    slug = re.sub(r"[^a-z0-9]", "", name_lower)
    if len(slug) > 3:
        guess = f"https://www.{slug}.com"
        if _url_resolves(guess):
            return guess

    return None


def _get_domain(url: str) -> str:
    m = re.search(r"https?://(?:www\.)?([^/]+)", url)
    return m.group(1) if m else ""


def _url_resolves(url: str, timeout: int = 6) -> bool:
    """HEAD request to check if a URL is live."""
    try:
        r = _requests.head(
            url, timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0"},
            allow_redirects=True,
        )
        return r.status_code < 400
    except Exception:
        return False


def _network_available() -> bool:
    try:
        socket.setdefaulttimeout(3)
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(("8.8.8.8", 53))
        return True
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────
#  LANGGRAPH NODE
# ─────────────────────────────────────────────────────────────────

def run_scout_node(state: AgentState) -> dict:
    """
    LangGraph node: Scout Agent.

    Searches DuckDuckGo for local shops in the retailer's city,
    extracts shop names via LLM, resolves URLs, and registers them
    as scrape targets in the competitor registry.

    Total competitors = planner (user's known) + scout (discovered local).
    """
    profile     = state["retailer_profile"]
    retailer_id = state["retailer_id"]
    plan        = state.get("execution_plan")

    city     = profile.location.split(",")[0].strip()
    category = profile.category

    print(f"\n[Scout] Searching for {category} shops in {city}...")

    if not _network_available():
        print("  [Scout] No network — skipping.")
        return {"current_node": "scout"}

    # Existing registered competitors — for dedup
    existing      = db.get_competitors(retailer_id)
    known_domains = {_get_domain(t["url"]) for t in existing}
    known_names   = {t["competitor_name"].lower() for t in existing}

    # ── Step 1: DuckDuckGo search ─────────────────────────────────
    queries = _build_queries(category, city)
    print(f"  [Scout] Running {len(queries)} queries for shops in {city}...")

    wrapper    = DuckDuckGoSearchAPIWrapper(max_results=8, time="y")
    ddg        = DuckDuckGoSearchResults(api_wrapper=wrapper, output_format="list")
    all_snippets = []
    seen_links   = set()

    for query in queries:
        for attempt in range(2):
            try:
                results = ddg.invoke(query)
                for r in (results if isinstance(results, list) else []):
                    link = r.get("link", "")
                    if link and link not in seen_links:
                        seen_links.add(link)
                        all_snippets.append(r)
                time.sleep(1.2)
                break
            except Exception as e:
                print(f"  [Scout] Query failed ({attempt+1}/2): {e}")
                time.sleep(2.0)

    if not all_snippets:
        print("  [Scout] No search results — skipping.")
        return {"current_node": "scout"}

    print(f"  [Scout] Got {len(all_snippets)} snippets — extracting shops...")

    # ── Step 2: LLM extracts shop names + URLs ────────────────────
    formatted = "\n\n".join([
        f"Title:   {r.get('title','')}\n"
        f"URL:     {r.get('link','')}\n"
        f"Snippet: {r.get('snippet','')}"
        for r in all_snippets[:20]
    ])

    try:
        shops = _build_extract_chain().invoke({
            "category": category,
            "city":     city,
            "results":  formatted,
        })
        if not isinstance(shops, list):
            shops = []
    except Exception as e:
        print(f"  [Scout] LLM extraction failed: {e}")
        return {"current_node": "scout"}

    print(f"  [Scout] LLM found {len(shops)} candidate shops")

    # ── Step 3: Resolve URLs, validate, deduplicate ───────────────
    new_targets: list[dict] = []
    added_names: set        = set()

    for shop in shops:
        if len(added_names) >= MAX_NEW_COMPETITORS:
            break

        name    = str(shop.get("shop_name", "")).strip()
        raw_url = str(shop.get("website", "")).strip()

        if not name or len(name) < 3:
            continue

        # Skip if already registered
        if name.lower() in known_names:
            print(f"  ↷ Already registered: {name}")
            continue
        if name.lower() in added_names:
            continue

        # Resolve to a working URL
        base_url = _resolve_url(name, raw_url)
        if not base_url:
            print(f"  ✗ No working URL for: {name}")
            continue

        domain = _get_domain(base_url)
        if domain in known_domains:
            print(f"  ↷ Domain already registered: {domain}")
            continue
        if any(bl in domain for bl in BLACKLIST_DOMAINS):
            print(f"  ✗ Blacklisted domain: {domain}")
            continue

        # Build one target per catalog product (same as planner)
        for product in profile.catalog:
            pname = product.get("name", "")
            sku   = product.get("sku", "")
            if not pname:
                continue
            new_targets.append({
                "competitor_name":      name,
                "url":                  base_url,
                "priority":             "medium",
                "scan_interval_hours":  24,
                "scrape_method":        "dynamic",
                "product_category":     category,
                "selector_config":      {},
                "source":               "scout_local",
                "notes":                f"Discovered in {city} via DDG",
                "catalog_sku":          sku,
                "catalog_product_name": pname,
            })

        added_names.add(name.lower())
        known_domains.add(domain)
        print(f"  ✓ {name:30} → {base_url}")

    if not new_targets:
        print("  [Scout] No new competitors found.")
        return {"current_node": "scout"}

    # ── Step 4: Save to DB + update execution plan ────────────────
    for t in new_targets:
        db.upsert_competitor(retailer_id, t)

    new_scrape_objs = list(plan.scrape_targets) if plan else []
    for t in new_targets:
        new_scrape_objs.append(
            ScrapeTarget(**{k: v for k, v in t.items()
                            if k in ScrapeTarget.model_fields})
        )

    updated_plan = ExecutionPlan(
        scrape_targets      = new_scrape_objs,
        priority_categories = plan.priority_categories if plan else profile.subcategories,
        strategy_framework  = plan.strategy_framework  if plan else profile.pricing_strategy,
        reasoning           = (plan.reasoning if plan else "") +
                              f" | Scout found {len(added_names)} local shop(s) in {city}.",
    )

    print(f"\n[Scout] Done — {len(added_names)} new local shop(s) added "
          f"({len(new_targets)} targets):")
    for n in sorted(added_names):
        print(f"  + {n.title()}")

    return {
        "execution_plan": updated_plan,
        "current_node":   "scout",
    }