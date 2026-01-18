"""
Polymarket Markets Service
Provides market listing and detail functions using Gamma API
"""

import requests
from typing import List, Optional
from datetime import datetime


GAMMA_API_URL = "https://gamma-api.polymarket.com"

# Categories to exclude (sports, price predictions are less predictable)
EXCLUDED_TAGS = {"sports", "nfl", "nba", "mlb", "soccer", "esports", "price"}

# Slug patterns to exclude (short-term price gambling markets)
EXCLUDED_SLUG_PATTERNS = ["up-or-down", "updown", "-15m-", "-1h-", "-4h-"]


def get_markets_list(
    limit: int = 50,
    min_liquidity: float = 5000,
    category: str = None
) -> List[dict]:
    """
    Fetch active markets list from Polymarket.
    
    Args:
        limit: Maximum number of markets to return (default 50, max 100)
        min_liquidity: Minimum liquidity in USD (default 5000)
        category: Optional category filter (politics, crypto, science, etc.)
    
    Returns:
        List of market dicts with: id, title, category, price, volume_24h, 
        liquidity, end_date
    """
    limit = min(limit, 100)
    
    params = {
        "closed": "false",
        "limit": limit * 2,  # Fetch more to filter
        "order": "liquidity",
        "ascending": "false"
    }
    
    try:
        response = requests.get(
            f"{GAMMA_API_URL}/events",
            params=params,
            timeout=15
        )
        response.raise_for_status()
        events = response.json()
    except Exception as e:
        print(f"Error fetching markets: {e}")
        return []
    
    markets = []
    
    for event in events:
        # Skip if no markets
        event_markets = event.get("markets", [])
        if not event_markets:
            continue
        
        # Skip closed events
        if event.get("closed", False):
            continue
        
        # Get liquidity
        liquidity = float(event.get("liquidity", 0) or 0)
        if liquidity < min_liquidity:
            continue
        
        # Check tags for exclusions
        tags = event.get("tags", [])
        tag_slugs = {t.get("slug", "").lower() for t in tags}
        
        if tag_slugs & EXCLUDED_TAGS:
            continue
        
        # Check slug for exclusion patterns (short-term price betting)
        event_slug = event.get("slug", "").lower()
        if any(pattern in event_slug for pattern in EXCLUDED_SLUG_PATTERNS):
            continue
        
        # Category filter
        if category:
            if category.lower() not in tag_slugs:
                continue
        
        # Get primary category
        primary_category = "other"
        for tag in tags:
            slug = tag.get("slug", "").lower()
            if slug in ["politics", "crypto", "science", "entertainment", 
                        "business", "tech", "finance"]:
                primary_category = slug
                break
        
        # Get the first active market's price
        active_market = None
        for m in event_markets:
            if m.get("active") and not m.get("closed"):
                active_market = m
                break
        
        if not active_market:
            continue
        
        # Parse YES price
        try:
            outcome_prices = active_market.get("outcomePrices", "[0, 0]")
            if isinstance(outcome_prices, str):
                import json
                prices = json.loads(outcome_prices)
            else:
                prices = outcome_prices
            yes_price = float(prices[0]) if prices else 0
        except:
            yes_price = 0
        
        markets.append({
            "id": event.get("id"),
            "slug": event.get("slug"),
            "title": event.get("title"),
            "category": primary_category,
            "price": yes_price,
            "volume_24h": float(event.get("volume24hr", 0) or 0),
            "liquidity": liquidity,
            "end_date": event.get("endDate"),
            "description": event.get("description", "")[:500]
        })
        
        if len(markets) >= limit:
            break
    
    return markets


def get_market_detail(market_id: str) -> Optional[dict]:
    """
    Get detailed information for a specific market.
    
    Args:
        market_id: Market/Event ID
    
    Returns:
        Dict with: id, title, description, rules, price, volume, 
        liquidity, end_date, created_date, outcomes
    """
    try:
        response = requests.get(
            f"{GAMMA_API_URL}/events/{market_id}",
            timeout=15
        )
        response.raise_for_status()
        event = response.json()
    except Exception as e:
        print(f"Error fetching market detail: {e}")
        return None
    
    if not event:
        return None
    
    markets = event.get("markets", [])
    if not markets:
        return None
    
    primary_market = markets[0]
    
    # Parse outcome prices
    try:
        outcome_prices = primary_market.get("outcomePrices", "[0, 0]")
        if isinstance(outcome_prices, str):
            import json
            prices = json.loads(outcome_prices)
        else:
            prices = outcome_prices
        yes_price = float(prices[0]) if len(prices) > 0 else 0
        no_price = float(prices[1]) if len(prices) > 1 else 0
    except:
        yes_price = 0
        no_price = 0
    
    # Parse outcomes
    try:
        outcomes_str = primary_market.get("outcomes", '["Yes", "No"]')
        if isinstance(outcomes_str, str):
            import json
            outcomes = json.loads(outcomes_str)
        else:
            outcomes = outcomes_str
    except:
        outcomes = ["Yes", "No"]
    
    return {
        "id": event.get("id"),
        "slug": event.get("slug"),
        "title": event.get("title"),
        "description": event.get("description", ""),
        "rules": primary_market.get("description", ""),
        "price": yes_price,
        "volume": float(primary_market.get("volumeNum", 0) or 0),
        "liquidity": float(event.get("liquidity", 0) or 0),
        "end_date": event.get("endDate"),
        "created_date": event.get("createdAt"),
        "outcomes": [
            {"name": outcomes[0] if outcomes else "Yes", "price": yes_price},
            {"name": outcomes[1] if len(outcomes) > 1 else "No", "price": no_price}
        ],
        "market_id": primary_market.get("id"),
        "condition_id": primary_market.get("conditionId"),
        "accepting_orders": primary_market.get("acceptingOrders", False)
    }


def get_market_by_slug(slug: str) -> Optional[dict]:
    """
    Get market detail by slug.
    
    Args:
        slug: Market slug (e.g., 'microstrategy-sell-any-bitcoin-in-2025')
    
    Returns:
        Market detail dict or None
    """
    try:
        response = requests.get(
            f"{GAMMA_API_URL}/events/slug/{slug}",
            timeout=15
        )
        response.raise_for_status()
        event = response.json()
    except Exception as e:
        print(f"Error fetching market by slug: {e}")
        return None
    
    if not event:
        return None
    
    # Reuse the detail parsing logic
    return get_market_detail(event.get("id"))


if __name__ == "__main__":
    # Test the module
    print("=" * 60)
    print("Testing Markets Module")
    print("=" * 60)
    
    print("\n[1] Fetching market list (limit=5)...")
    markets = get_markets_list(limit=5, min_liquidity=10000)
    for i, m in enumerate(markets, 1):
        print(f"\n  {i}. {m['title'][:50]}...")
        print(f"     Category: {m['category']}")
        print(f"     Price: ${m['price']:.2f}")
        print(f"     Liquidity: ${m['liquidity']:,.0f}")
    
    if markets:
        print(f"\n[2] Fetching detail for first market...")
        detail = get_market_detail(markets[0]["id"])
        if detail:
            print(f"  Title: {detail['title']}")
            print(f"  End Date: {detail['end_date']}")
            print(f"  Outcomes: {detail['outcomes']}")
    
    print("\n" + "=" * 60)
    print("Test complete!")
