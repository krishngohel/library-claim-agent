"""Pricing rules. Every figure we output is one real listing, so it can be clicked and checked.

Book replacement cost = the median *new* listing whose title matches the book
                        (Google Shopping via SerpAPI if a key is set, plus eBay new listings).
Book used value       = the median *used* listing whose title matches the book (eBay used + Shopping used).
We report the median listing itself (its exact price and URL), not an average of
several, so the adjuster can open the one page the number came from.

Fallbacks, in order: local market -> other market converted at the ECB rate (labelled
converted=true) -> no price (amount null, excluded from totals, sent to review).
"""

import re

from rapidfuzz import fuzz

from app import config, prices

CONDITION_ASSUMED = "Good - used, complete, no visible damage on the spine in the sweep frames"


def median_listing(offers: list[dict]) -> dict | None:
    if not offers:
        return None
    ordered = sorted(offers, key=lambda o: o["amount"])
    return ordered[(len(ordered) - 1) // 2]   # lower median: always a real listing


def matching(offers: list[dict], title: str, author: str = "") -> list[dict]:
    """Keep only listings whose title clearly contains the book title (drops study guides, box sets, etc.).

    A short title like "Emma" also appears inside unrelated listings ("Gemma's Kitchen"), so for short
    titles the author's surname must be in the listing too."""
    surname = author.split(",")[0].strip().split(" ")[-1].lower() if author.strip() else ""
    out = []
    for o in offers:
        listing = o["title"].lower()
        if fuzz.partial_ratio(title.lower(), listing) < 85:
            continue
        whole_word = r"(?![a-z0-9])"   # the next character is not a letter or digit
        starts_with_title = re.match(re.escape(title.lower()) + whole_word, listing) is not None
        has_surname = bool(surname) and re.search(r"(?<![a-z0-9])" + re.escape(surname) + whole_word, listing) is not None
        if len(title) < 12 and not has_surname and not starts_with_title:
            continue
        out.append(o)
    return out


def other_market(country: str) -> str:
    return "GB" if country != "GB" else "US"


async def _find(sweep, query: str, title: str, author: str, country: str) -> tuple[dict | None, dict | None]:
    """(median new listing, median used listing) in one country, or None for each."""
    shop = matching(await prices.shopping_offers(sweep, query, country), title, author)
    new = [o for o in shop if not o["used"]] + matching(await prices.ebay_offers(sweep, query, country, "NEW"), title, author)
    used = [o for o in shop if o["used"]] + matching(await prices.ebay_offers(sweep, query, country, "USED"), title, author)
    return median_listing(new), median_listing(used)


async def _convert(listing: dict | None, to_ccy: str) -> dict | None:
    if listing is None:
        return None
    fx = await prices.fx_rate(listing["currency"], to_ccy)
    if fx is None:
        return None
    return {**listing, "amount": round(listing["amount"] * fx["rate"], 2), "currency": to_ccy, "converted": True,
            "source": f"{listing['source']}, converted from {listing['currency']} at {fx['rate']} "
                      f"(ECB rate {fx['date']}, {fx['url']})",
            "original_amount": listing["amount"], "original_currency": listing["currency"]}


async def price_book(sweep, book: dict, country: str, currency: str) -> tuple[dict, dict]:
    """Return (replacement_cost, used_value) in the packet's format. amount=None means no price found."""
    query = book["isbn"] or f"{book['title']} {book['author'].split(',')[0]} book".strip()
    new, used = await _find(sweep, query, book["title"], book["author"], country)
    if new is None or used is None:   # fill only the missing side from the other market
        other_new, other_used = await _find(sweep, query, book["title"], book["author"], other_market(country))
        new = new or await _convert(other_new, currency)
        used = used or await _convert(other_used, currency)

    replacement = {"amount": None, "source": "no listing found in any market", "url": "", "retrieved_at": "",
                   "converted": False}
    if new:
        replacement = {"amount": new["amount"], "source": new["source"], "url": new["url"],
                       "retrieved_at": new["retrieved_at"], "converted": new.get("converted", False),
                       "listing_title": new["title"]}
    used_value = {"amount": None, "source": "no listing found in any market", "url": "", "retrieved_at": "",
                  "condition_assumed": CONDITION_ASSUMED}
    if used:
        used_value = {"amount": used["amount"], "source": used["source"], "url": used["url"],
                      "retrieved_at": used["retrieved_at"], "condition_assumed": CONDITION_ASSUMED,
                      "converted": used.get("converted", False), "listing_title": used["title"],
                      "listing_condition": used.get("condition", "used")}
    return replacement, used_value


def appraisal_reasons(book: dict, statements: list[dict]) -> list[str]:
    """Reasons a book must go to a human appraiser instead of being auto-priced."""
    reasons = []
    if book.get("looks_antiquarian"):
        reasons.append("binding looks antiquarian or signed in the frame")
    for s in statements:
        if s["kind"] in ("first_edition", "signed", "rare") and book.get("title") and \
                fuzz.partial_ratio(s["about"].lower(), book["title"].lower()) >= 80:
            reasons.append(f"policyholder said: \"{s['quote']}\"")
    return reasons


def over_threshold(replacement: dict, used: dict) -> bool:
    amounts = [p["amount"] for p in (replacement, used) if p["amount"] is not None]
    return any(a > config.APPRAISAL_THRESHOLD for a in amounts)


def _percentile_listing(offers: list[dict], q: float) -> dict:
    ordered = sorted(offers, key=lambda o: o["amount"])
    return ordered[round(q * (len(ordered) - 1))]


async def price_item(sweep, item: dict, country: str, currency: str) -> tuple[str, dict]:
    """(status, replacement_cost) for a non-book item.

    Known brand/model -> status 'priced', low = high = the median matching listing.
    Unknown brand     -> status 'range', low/high = 25th/75th percentile listings for the description.
    """
    query = item["brand_model"] or f"{item['material']} {item['description']}".strip()
    offers = [o for o in await prices.shopping_offers(sweep, query, country) if not o["used"]]
    offers += await prices.ebay_offers(sweep, query, country, "NEW")
    empty = {"low": None, "high": None, "source": "no listing found", "url": "", "retrieved_at": ""}
    if not offers:
        return "range", empty
    if item["brand_model"]:
        brand_matches = [o for o in offers if fuzz.partial_ratio(item["brand_model"].lower(), o["title"].lower()) >= 80]
        pick = median_listing(brand_matches)
        if pick:
            return "priced", {"low": pick["amount"], "high": pick["amount"], "source": pick["source"],
                              "url": pick["url"], "retrieved_at": pick["retrieved_at"], "listing_title": pick["title"]}
    low, high = _percentile_listing(offers, 0.25), _percentile_listing(offers, 0.75)
    return "range", {"low": low["amount"], "high": high["amount"],
                     "source": f"{len(offers)} new listings for '{query}' (25th-75th percentile): "
                               f"low = {low['source']}, high = {high['source']}",
                     "url": low["url"], "url_high": high["url"], "retrieved_at": low["retrieved_at"],
                     "query": query}
