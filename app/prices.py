"""Price sources. Each function returns real listings (price + URL + retrieval date) or nothing.

  shopping_offers - Google Shopping results for a country, via SerpAPI (new retail prices)
  ebay_offers     - eBay Browse API (free developer key), NEW or USED, per-country marketplace
  fx_rate         - ECB reference rate via frankfurter.dev, for labelled conversions

No function here ever makes up a number. A missing key or empty result returns [].
"""

import base64
import time
from datetime import date

import httpx

from app import config
from app.http_cache import get_json

SYMBOLS = {"$": "USD", "£": "GBP", "€": "EUR", "C$": "CAD", "CA$": "CAD", "A$": "AUD", "AU$": "AUD"}


def _currency_of(price_text: str) -> str:
    for symbol in sorted(SYMBOLS, key=len, reverse=True):   # check "CA$" before "$"
        if price_text.strip().startswith(symbol):
            return SYMBOLS[symbol]
    return ""


async def shopping_offers(sweep, query: str, country: str) -> list[dict]:
    if not config.SERPAPI_KEY:
        return []
    currency, gl = config.LOCALES[country]["currency"], config.LOCALES[country]["google"]
    params = {"engine": "google_shopping", "q": query, "gl": gl, "hl": "en", "api_key": config.SERPAPI_KEY}
    data, retrieved_at = await get_json("https://serpapi.com/search.json", params)
    sweep.usage["serpapi"] += 1
    offers = []
    for r in (data or {}).get("shopping_results", []):
        price, text = r.get("extracted_price"), r.get("price", "")
        if price is None or _currency_of(text) != currency:
            continue   # skip listings shown in a different currency
        offers.append({
            "title": r.get("title", ""), "amount": float(price), "currency": currency,
            "source": f"Google Shopping ({gl}) - {r.get('source', 'unknown seller')}",
            "url": r.get("product_link") or r.get("link") or "",
            "used": bool(r.get("second_hand_condition")),
            "retrieved_at": retrieved_at,
        })
    return offers


_token = {"value": "", "expires": 0.0}


async def _ebay_token() -> str:
    """OAuth app token, reused until a minute before it expires (eBay issues them for 2 hours)."""
    if time.time() < _token["expires"]:
        return _token["value"]
    creds = base64.b64encode(f"{config.EBAY_CLIENT_ID}:{config.EBAY_CLIENT_SECRET}".encode()).decode()
    async with httpx.AsyncClient(timeout=20) as http:
        r = await http.post("https://api.ebay.com/identity/v1/oauth2/token",
                            headers={"Authorization": f"Basic {creds}",
                                     "Content-Type": "application/x-www-form-urlencoded"},
                            data={"grant_type": "client_credentials",
                                  "scope": "https://api.ebay.com/oauth/api_scope"})
    r.raise_for_status()
    _token.update(value=r.json()["access_token"], expires=time.time() + r.json()["expires_in"] - 60)
    return _token["value"]


async def ebay_offers(sweep, query: str, country: str, condition: str) -> list[dict]:
    """Fixed-price eBay listings in one condition ("NEW" or "USED") on the country's marketplace."""
    if not (config.EBAY_CLIENT_ID and config.EBAY_CLIENT_SECRET):
        return []
    currency, marketplace = config.LOCALES[country]["currency"], config.LOCALES[country]["ebay"]
    try:
        token = await _ebay_token()
    except httpx.HTTPError:
        return []
    data, retrieved_at = await get_json(
        "https://api.ebay.com/buy/browse/v1/item_summary/search",
        {"q": query, "limit": 20, "filter": f"conditions:{{{condition}}},buyingOptions:{{FIXED_PRICE}}"},
        headers={"Authorization": f"Bearer {token}", "X-EBAY-C-MARKETPLACE-ID": marketplace},
        cache_key_extra=marketplace,
    )
    sweep.usage["ebay"] += 1
    offers = []
    for item in (data or {}).get("itemSummaries", []):
        price = item.get("price", {})
        if price.get("currency") != currency:
            continue
        offers.append({
            "title": item.get("title", ""), "amount": float(price["value"]), "currency": currency,
            "source": f"eBay {marketplace} ({condition.lower()}, fixed price)", "url": item.get("itemWebUrl", ""),
            "condition": item.get("condition", condition.title()), "used": condition == "USED",
            "retrieved_at": retrieved_at,
        })
    return offers


async def fx_rate(from_ccy: str, to_ccy: str) -> dict | None:
    """ECB reference rate. Cached per day so all conversions in one sweep use the same rate."""
    if from_ccy == to_ccy:
        return {"rate": 1.0, "date": "", "url": ""}
    url = "https://api.frankfurter.dev/v1/latest"
    data, retrieved_at = await get_json(url, {"from": from_ccy, "to": to_ccy}, cache_key_extra=str(date.today()))
    if not data or to_ccy not in data.get("rates", {}):
        return None
    return {"rate": data["rates"][to_ccy], "date": data.get("date", ""),
            "url": f"{url}?from={from_ccy}&to={to_ccy}", "retrieved_at": retrieved_at}
