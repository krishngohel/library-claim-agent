"""Resolve spine text to a real catalogue record (Open Library, free, no key).

Rules:
  - We only search with text the vision model actually read. Unreadable spines never get here.
  - A match needs a strong fuzzy score on the title. Weak matches keep the spine text,
    get a low confidence, and go to the review queue.
  - An exact edition (ISBN, height) is chosen only when the publisher read on the spine
    matches exactly one edition. Several matches -> publisher only, no ISBN.
"""

import re

from rapidfuzz import fuzz

from app.http_cache import get_json

SEARCH = "https://openlibrary.org/search.json"
STRONG_MATCH = 85   # title similarity (0-100) needed to call it identified
WEAK_MATCH = 60


def _score(spine_title: str, spine_author: str, doc: dict) -> float:
    title_score = fuzz.token_set_ratio(spine_title.lower(), doc.get("title", "").lower())
    if not spine_author:
        return title_score
    authors = " ".join(doc.get("author_name", [])).lower()
    author_score = fuzz.partial_ratio(spine_author.lower(), authors) if authors else 0
    return 0.75 * title_score + 0.25 * author_score


def parse_height_cm(dimensions: str) -> float | None:
    """'20 x 13 x 2 centimeters' or '8.2 x 5.4 x 1 inches' -> height in cm (largest of the first two numbers)."""
    numbers = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", dimensions or "")]
    if len(numbers) < 2:
        return None
    height = max(numbers[:2])
    if "inch" in dimensions.lower():
        height *= 2.54
    elif "mm" in dimensions.lower() or "millimet" in dimensions.lower():
        height /= 10
    return round(height, 1)


async def _editions_for_publisher(work_key: str, publisher: str) -> list[dict]:
    """All editions of this work whose publisher matches the publisher read on the spine."""
    data, _ = await get_json(f"https://openlibrary.org{work_key}/editions.json", {"limit": 200})
    return [ed for ed in (data or {}).get("entries", [])
            if any(fuzz.partial_ratio(publisher.lower(), p.lower()) >= 85 for p in ed.get("publishers", []))]


async def identify(title: str, author: str, publisher: str) -> dict:
    """Return a dict of catalogue fields. Empty strings mean 'not known'."""
    params = {"q": f"{title} {author}".strip(), "limit": 5,
              "fields": "key,title,author_name,first_publish_year,isbn,publisher"}
    data, retrieved_at = await get_json(SEARCH, params)
    docs = (data or {}).get("docs", [])
    scored = sorted(((_score(title, author, d), d) for d in docs), key=lambda x: x[0], reverse=True)

    result = {"title": title, "author": author, "edition": "", "isbn": "", "catalog_url": "",
              "catalog_height_cm": None, "first_publish_year": None, "match_score": 0, "resolved": False}
    if not scored or scored[0][0] < WEAK_MATCH:
        return result   # keep exactly what was read; nothing invented

    score, doc = scored[0]
    result.update(
        title=doc.get("title", title),
        author=", ".join(doc.get("author_name", [])) or author,
        first_publish_year=doc.get("first_publish_year"),
        catalog_url=f"https://openlibrary.org{doc['key']}",
        match_score=round(score),
        resolved=score >= STRONG_MATCH,
    )
    if result["resolved"] and publisher:
        editions = await _editions_for_publisher(doc["key"], publisher)
        if len(editions) == 1:
            # Only one edition from this publisher: the spine evidence pins down the exact edition.
            ed = editions[0]
            isbns = ed.get("isbn_13") or ed.get("isbn_10") or []
            result.update(
                isbn=isbns[0] if isbns else "",
                edition=f"{', '.join(ed.get('publishers', []))} {ed.get('publish_date', '')}".strip(),
                catalog_height_cm=parse_height_cm(ed.get("physical_dimensions", "")),
                catalog_url=f"https://openlibrary.org{ed['key']}",
            )
        elif editions:
            # Several editions from this publisher: name the publisher, but do not pick an ISBN.
            result["edition"] = f"{publisher} (one of {len(editions)} editions; exact edition not readable from spine)"
    return result
