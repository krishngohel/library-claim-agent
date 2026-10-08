"""The output contract from the brief, as a checker. Extra fields are allowed; missing ones are not.

    python -m tests.contract sweeps/<id>/claim_packet.json
"""

import json
import sys
from pathlib import Path

CONTRACT = {
    "sweep": ["id", "captured_at", "device", "duration_s", "country", "currency"],
    "room": ["length_m", "width_m", "height_m", "floor_area_m2", "wall_area_m2", "shelved_wall_area_m2",
             "scale_method", "confidence"],
    "totals": ["book_count", "books_identified", "books_unidentified", "shelf_run_m", "books_replacement_cost",
               "books_used_value", "items_replacement_cost_low", "items_replacement_cost_high", "excluded_from_totals"],
}
BOOK = ["id", "shelf", "position", "frame_ref", "status", "title", "author", "edition", "isbn", "spine_height_cm",
        "spine_thickness_cm", "id_confidence", "replacement_cost", "used_value"]
BOOK_REPLACEMENT = ["amount", "source", "url", "retrieved_at", "converted"]
BOOK_USED = ["amount", "source", "url", "retrieved_at", "condition_assumed"]
ITEM = ["id", "category", "description", "brand_model", "frame_ref", "dimensions_cm", "status", "replacement_cost",
        "confidence"]
ITEM_PRICE = ["low", "high", "source", "url", "retrieved_at"]


def problems(packet: dict, sweep_dir: Path | None = None) -> list[str]:
    out = []

    def need(obj, keys, where):
        out.extend(f"{where}: missing '{k}'" for k in keys if k not in obj)

    for section, keys in CONTRACT.items():
        need(packet.get(section, {}), keys, section)
    for b in packet.get("books", []):
        need(b, BOOK, b.get("id", "book"))
        need(b.get("replacement_cost", {}), BOOK_REPLACEMENT, f"{b.get('id')}.replacement_cost")
        need(b.get("used_value", {}), BOOK_USED, f"{b.get('id')}.used_value")
        if b.get("status") not in ("identified", "unidentified", "needs_appraisal"):
            out.append(f"{b.get('id')}: bad status {b.get('status')}")
        if b.get("status") == "unidentified" and b.get("title"):
            out.append(f"{b['id']}: unidentified but has a title")
        for price in (b.get("replacement_cost", {}), b.get("used_value", {})):
            if price.get("amount") is not None and not (price.get("url") and price.get("retrieved_at")):
                out.append(f"{b['id']}: price without url/date")
        if b.get("status") == "needs_appraisal" and b["replacement_cost"].get("amount") is not None:
            out.append(f"{b['id']}: appraisal item was auto-priced")
    for i in packet.get("items", []):
        need(i, ITEM, i.get("id", "item"))
        need(i.get("dimensions_cm", {}), ["w", "h", "d"], f"{i.get('id')}.dimensions_cm")
        need(i.get("replacement_cost", {}), ITEM_PRICE, f"{i.get('id')}.replacement_cost")
        if i.get("status") not in ("priced", "range", "needs_appraisal"):
            out.append(f"{i.get('id')}: bad status {i.get('status')}")
        if i.get("replacement_cost", {}).get("low") is not None and not i["replacement_cost"].get("url"):
            out.append(f"{i['id']}: price without url")
    for r in packet.get("review_queue", []):
        need(r, ["ref_id", "reason"], "review_queue entry")
    if sweep_dir:   # every frame_ref must open
        for line in packet.get("books", []) + packet.get("items", []):
            if not (sweep_dir / "frames" / line["frame_ref"].split("@")[0]).exists():
                out.append(f"{line['id']}: frame_ref {line['frame_ref']} does not exist")
    # totals must add up from the lines
    priced = [b for b in packet.get("books", []) if b["status"] != "needs_appraisal"]
    total = round(sum(b["replacement_cost"]["amount"] or 0 for b in priced), 2)
    if abs(total - packet["totals"]["books_replacement_cost"]) > 0.01:
        out.append(f"totals: books_replacement_cost {packet['totals']['books_replacement_cost']} != lines {total}")
    if packet["totals"]["book_count"] != len(packet.get("books", [])):
        out.append("totals: book_count does not match the number of book lines")
    return out


if __name__ == "__main__":
    path = Path(sys.argv[1])
    found = problems(json.loads(path.read_text(encoding="utf-8")), path.parent)
    print("\n".join(found) or "packet meets the output contract")
    sys.exit(1 if found else 0)
