"""Totals and review queue. Plain arithmetic over the lines; no model involved.

The rule from the brief: totals are computed in code from the lines. Anything without
a price, or flagged for appraisal, is left out of the totals and counted in
`excluded_from_totals` so the adjuster can see what is missing.
"""


def _amount(price: dict, key: str = "amount") -> float | None:
    return price.get(key) if price else None


def totals(books: list[dict], items: list[dict]) -> dict:
    priced_books = [b for b in books if b["status"] != "needs_appraisal"]
    book_repl = [_amount(b["replacement_cost"]) for b in priced_books]
    book_used = [_amount(b["used_value"]) for b in priced_books]
    priced_items = [i for i in items if i["status"] != "needs_appraisal"]
    item_low = [_amount(i["replacement_cost"], "low") for i in priced_items]
    item_high = [_amount(i["replacement_cost"], "high") for i in priced_items]

    excluded_books = [b["id"] for b in books
                      if b["status"] == "needs_appraisal" or _amount(b["replacement_cost"]) is None]
    excluded_items = [i["id"] for i in items
                      if i["status"] == "needs_appraisal" or _amount(i["replacement_cost"], "low") is None]
    return {
        "book_count": len(books),
        "books_identified": sum(b["status"] == "identified" for b in books),
        "books_unidentified": sum(b["status"] == "unidentified" for b in books),
        "books_needs_appraisal": sum(b["status"] == "needs_appraisal" for b in books),
        "shelf_run_m": round(sum(b["spine_thickness_cm"] or 0 for b in books) / 100, 2),
        "books_without_thickness": sum(not b["spine_thickness_cm"] for b in books),
        "books_replacement_cost": round(sum(a for a in book_repl if a is not None), 2),
        "books_used_value": round(sum(a for a in book_used if a is not None), 2),
        "items_replacement_cost_low": round(sum(a for a in item_low if a is not None), 2),
        "items_replacement_cost_high": round(sum(a for a in item_high if a is not None), 2),
        "excluded_from_totals": len(excluded_books) + len(excluded_items),
        "excluded_ids": excluded_books + excluded_items,
    }


def review_queue(books: list[dict], items: list[dict], room: dict) -> list[dict]:
    queue = []

    def add(ref_id, reason):
        queue.append({"ref_id": ref_id, "reason": reason})

    for b in books:
        if b["status"] == "unidentified":
            add(b["id"], "spine not legible in any frame; title left blank")
        elif b["status"] == "needs_appraisal":
            add(b["id"], "needs human appraisal: " + "; ".join(b["appraisal_reasons"]))
        elif b["id_confidence"] < 0.85:
            add(b["id"], f"weak catalogue match (confidence {b['id_confidence']}); title as read from spine")
        if not b["spine_height_cm"]:
            add(b["id"], "no metric scale available for this frame; dimensions left blank")
        elif b["scale_method"].startswith("shelf_median"):
            add(b["id"], "no reference in this frame; scale borrowed from other frames of the same shelf")
        elif b["scale_method"].startswith("standard_format"):
            add(b["id"], "no reference anywhere on this shelf; size assumes a standard book height (low confidence)")
        if b["status"] != "needs_appraisal":
            if b["replacement_cost"]["amount"] is None:
                add(b["id"], "no replacement price found in any market; excluded from totals")
            elif b["replacement_cost"]["converted"]:
                add(b["id"], "no local new listing; replacement price converted from another market")
            if b["used_value"]["amount"] is None:
                add(b["id"], "no used listing found; used value blank")
    for i in items:
        if i["status"] == "needs_appraisal":
            add(i["id"], i.get("appraisal_reason", "needs human appraisal"))
        elif i["replacement_cost"]["low"] is None:
            add(i["id"], "no listing found; excluded from totals")
        elif i["status"] == "range":
            add(i["id"], "brand/model not legible; price is a range for similar items")
        if not i["dimensions_cm"]["w"]:
            add(i["id"], "no metric scale available; dimensions left blank")
    if room["confidence"] < 0.6:
        add("room", "room measurement low confidence: " + "; ".join(room.get("shape_notes", [])))
    return queue
