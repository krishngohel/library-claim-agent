"""After the sweep: turn saved frames + scans into claim_packet.json and report.html.

Stages run in this order, each timed and logged to sweeps/<id>/stages.jsonl:
  1. books_consolidate  one list of distinct spines per shelf        (vision model)
  2. books_identify     spine text -> catalogue record                (Open Library)
  3. measure            pixel boxes -> centimetres; room dimensions    (code only)
  4. items_consolidate  one list of distinct non-book objects          (vision model)
  5. price              listings for books and items                   (SerpAPI, eBay, ECB)
  6. packet             totals + review queue + report                 (code only)
"""

import json
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from statistics import median

import cv2
from rapidfuzz import fuzz

from app import catalog, config, report, scale, valuation, vision
from app.packet import review_queue, totals


@contextmanager
def timed(sweep, stage: str):
    start = time.monotonic()
    yield
    sweep.stage_seconds[stage] = round(time.monotonic() - start, 1)


def image_size(frame) -> tuple[int, int]:
    h, w = cv2.imread(str(frame.path)).shape[:2]
    return w, h


def spread(frames: list, n: int) -> list:
    """Pick n frames spread evenly through the list (keeps model input small)."""
    if len(frames) <= n:
        return frames
    step = len(frames) / n
    return [frames[int(i * step)] for i in range(n)]


# ---------- 1. books: consolidate ----------

def shelf_segments(sweep) -> list[str]:
    segments = {f.segment for f in sweep.frames if f.ref in sweep.scans and f.segment.startswith("shelf")}
    return sorted(segments - set(sweep.skipped_segments))


async def consolidate_all_shelves(sweep) -> None:
    todo = [s for s in shelf_segments(sweep) if s not in sweep.shelf_books]
    results = await vision.gather_limited(
        [vision.consolidate_shelf(sweep, s, spread(scanned_frames(sweep, s), 10)) for s in todo])
    sweep.shelf_books.update(dict(zip(todo, results)))


def scanned_frames(sweep, segment: str | None = None) -> list:
    return [f for f in sweep.frames if f.ref in sweep.scans and (segment is None or f.segment == segment)]


# ---------- 2. books: identify ----------

async def identify_books(sweep) -> list[dict]:
    spines = [{**s, "shelf": shelf, "position": i + 1}
              for shelf in shelf_segments(sweep) for i, s in enumerate(sweep.shelf_books.get(shelf, []))]

    async def one(s):
        if not (s["legible"] and s["title"].strip()):
            return {**s, "resolved": False, "match_score": 0, "catalog_height_cm": None, "isbn": "", "edition": "",
                    "catalog_url": "", "first_publish_year": None, "title": "", "author": ""}
        found = await catalog.identify(s["title"], s["author"], s["publisher"])
        return {**s, **found, "spine_text": s["title"]}

    return await vision.gather_limited([one(s) for s in spines], limit=8)


# ---------- 3. measure ----------

def frame_scale(sweep, frame, door_cm: float, books_by_frame: dict) -> tuple[float | None, str]:
    """Pixels-per-cm for one frame: references first, then catalogue-sized books in the frame."""
    w, h = image_size(frame)
    px, method = scale.scale_from_references(sweep.scans[frame.ref]["references"], w, h, door_cm)
    if px:
        return px, method
    px = scale.scale_from_known_books(books_by_frame.get(frame.ref, []), w, h)
    return (px, "catalog_book_height") if px else (None, "")


def measure_books(sweep, books: list[dict]) -> None:
    door_cm = config.LOCALES[sweep.country][3]
    by_frame = {}
    for b in books:
        by_frame.setdefault(b["frame_ref"], []).append(b)
    scales = {ref: frame_scale(sweep, sweep.frame(ref), door_cm, by_frame) for ref in by_frame}

    for shelf in {b["shelf"] for b in books}:
        on_shelf = [b for b in books if b["shelf"] == shelf]
        known = [scales[b["frame_ref"]][0] for b in on_shelf if scales[b["frame_ref"]][0]]
        if known:
            fallback, fallback_method = median(known), "shelf_median_of_other_frames"
        else:   # nothing on this shelf has a reference: fall back to a standard book height
            w, h = image_size(sweep.frame(on_shelf[0]["frame_ref"]))
            fallback = scale.scale_from_standard_format(on_shelf, w, h, config.STANDARD_BOOK_HEIGHT_CM)
            fallback_method = f"standard_format_assumption (median spine = {config.STANDARD_BOOK_HEIGHT_CM} cm)"
        for b in on_shelf:
            px, method = scales[b["frame_ref"]]
            if not px and fallback:
                px, method = fallback, fallback_method
            b["scale_method"] = method
            b["spine_height_cm"], b["spine_thickness_cm"] = (None, None)   # unknown, not zero
            if px:
                w, h = image_size(sweep.frame(b["frame_ref"]))
                b["spine_height_cm"], b["spine_thickness_cm"] = scale.spine_cm(b, px, w, h)


def measure_items(sweep, items: list[dict]) -> None:
    door_cm = config.LOCALES[sweep.country][3]
    for it in items:
        frame = sweep.frame(it["frame_ref"])
        px, method = frame_scale(sweep, frame, door_cm, {})
        w, h = image_size(frame)
        it["dimensions_cm"] = scale.item_cm(it["box_2d"], px, w, h) if px else {"w": None, "h": None, "d": None}
        it["scale_method"] = method


def measure_room(sweep, items: list[dict]) -> dict:
    """Room size from the whole-wall frames (segments wall_1..wall_4). The maths is in scale.walls_from_views."""
    door_cm = config.LOCALES[sweep.country][3]
    views, frames_used = [], {}
    for f in scanned_frames(sweep):
        if not f.segment.startswith("wall_"):
            continue
        scan = sweep.scans[f.ref]
        doors = [r for r in scan["references"] if r["kind"] == "door" and r["fully_visible"] and r["facing_camera"]]
        w, h = image_size(f)
        views.append({"wall": int(f.segment[5:]), "wall_box": scan["wall_box"],
                      "door_box": doors[0]["box_2d"] if doors else None, "w": w, "h": h})
        if scale.plausible_wall_box(scan["wall_box"]):
            frames_used.setdefault(f.segment, []).append(f.ref)

    per_wall, ceiling = scale.walls_from_views(views, door_cm)
    room = scale.room_from_walls(per_wall)
    shelving = [i for i in items if i["category"] == "shelving" and i["dimensions_cm"]["w"]]
    # Shelving front area = wall area it covers. Units without scale are left out (and queued).
    shelved = sum(i["dimensions_cm"]["w"] * i["dimensions_cm"]["h"] for i in shelving) / 10000
    room.update(
        shelved_wall_area_m2=round(shelved, 2),
        floor_area_ft2=scale.m2_to_ft2(room["floor_area_m2"]) if room["floor_area_m2"] else None,
        wall_area_ft2=scale.m2_to_ft2(room["wall_area_m2"]) if room["wall_area_m2"] else None,
        shelved_wall_area_ft2=scale.m2_to_ft2(shelved),
        scale_method=(f"ceiling height from door(s) = {door_cm} cm ({sweep.country} standard), median over "
                      f"{sum(1 for v in views if v['door_box'])} door views; each wall's width = its width:height "
                      "ratio x ceiling height") if ceiling else "none: no door seen in a whole-wall view",
        wall_frames=frames_used,
    )
    return room


async def consolidate_items(sweep) -> list[dict]:
    """One entry per physical object.

    Step 1 (model): within each wall/shelf segment, merge sightings of the same object across frames.
    Step 2 (code):  across segments, merge only sightings that could physically be one object.
    """
    segments = sorted({f.segment for f in scanned_frames(sweep)} - {"start"} - set(sweep.skipped_segments))
    per_segment = await vision.gather_limited(
        [vision.consolidate_items(sweep, spread(scanned_frames(sweep, s), 8)) for s in segments])
    sightings = [{**item, "segment": seg} for seg, items in zip(segments, per_segment) for item in items]
    return merge_across_segments([i for i in sightings if is_contents(i)])


def near_side_edge(box: list[int]) -> bool:
    """Within 15% of the left or right edge of the frame (boxes are on a 0-1000 grid)."""
    return box[1] < 150 or box[3] > 850


def could_be_same_object(a: dict, b: dict) -> bool:
    """Can sightings a and b be one physical object? Decided from where they were filmed:
      - same segment: yes (the model already merged within a segment, so this only catches leftovers)
      - a wall and a shelf: yes (a bookcase shows in the wall view and in its own close-up)
      - neighbouring walls (1-2, 2-3, 3-4, 4-1): only if it sits near a side edge in both views (a corner)
      - opposite walls, or two different shelf units: never
    Pieces of art the agent asked about separately (different ART ids) are never merged."""
    if a.get("art_id") and b.get("art_id") and a["art_id"] != b["art_id"]:
        return False
    sa, sb = a["segment"], b["segment"]
    if sa == sb:
        return True
    if sa.startswith("wall_") and sb.startswith("wall_"):
        neighbours = abs(int(sa[5:]) - int(sb[5:])) in (1, 3)
        return neighbours and near_side_edge(a["box_2d"]) and near_side_edge(b["box_2d"])
    return sa.startswith("wall_") != sb.startswith("wall_")


def merge_across_segments(sightings: list[dict]) -> list[dict]:
    merged: list[dict] = []
    for item in sightings:
        match = next((m for m in merged if m["category"] == item["category"] and could_be_same_object(m, item)
                      and fuzz.token_set_ratio(m["description"].lower(), item["description"].lower()) >= 60), None)
        if match is None:
            merged.append(item)
        elif scale.box_area(item["box_2d"]) > scale.box_area(match["box_2d"]):   # keep the fuller view
            merged[merged.index(match)] = {**item, "art_id": item.get("art_id") or match.get("art_id", "")}
    return merged


BUILDING_PARTS = ("door", "window", "outlet", "socket", "light switch", "switch plate", "radiator", "vent")


def is_contents(item: dict) -> bool:
    """Doors, windows, outlets etc. belong to the building, not the policyholder's contents."""
    text = item["description"].lower()
    return item["category"] in ("shelving", "art", "portrait", "rug", "lamp") or not any(w in text for w in BUILDING_PARTS)


# ---------- 5. price ----------

async def price_books(sweep, books: list[dict], country: str, currency: str) -> list[dict]:
    async def one(b):
        b = dict(b)
        b["appraisal_reasons"] = valuation.appraisal_reasons(b, sweep.statements)
        if not b["title"] or b["appraisal_reasons"]:
            empty = {"amount": None, "source": "", "url": "", "retrieved_at": ""}
            b["replacement_cost"] = {**empty, "converted": False}
            b["used_value"] = {**empty, "condition_assumed": ""}
            return b
        b["replacement_cost"], b["used_value"] = await valuation.price_book(sweep, b, country, currency)
        if valuation.over_threshold(b["replacement_cost"], b["used_value"]):
            b["appraisal_reasons"].append(f"listed above the {config.APPRAISAL_THRESHOLD:.0f} {currency} threshold")
        return b

    return await vision.gather_limited([one(b) for b in books], limit=6)


def to_packet_book(b: dict, n: int) -> dict:
    if b["appraisal_reasons"]:
        status = "needs_appraisal"
    elif b["title"]:
        status = "identified"
    else:
        status = "unidentified"
    confidence = round(b["match_score"] / 100, 2) if b["resolved"] else round(min(b["match_score"], 60) / 100, 2)
    return {
        "id": f"B{n:03d}", "shelf": b["shelf"], "position": b["position"], "row": b.get("row"),
        "frame_ref": b["frame_ref"], "box_2d": b["box_2d"], "status": status,
        "title": b["title"], "author": b["author"], "edition": b["edition"], "isbn": b["isbn"],
        "spine_text_read": b.get("spine_text", ""), "catalog_url": b["catalog_url"],
        "spine_height_cm": b["spine_height_cm"], "spine_thickness_cm": b["spine_thickness_cm"],
        "scale_method": b["scale_method"], "orientation": b["orientation"],
        "id_confidence": confidence if b["title"] else 0,
        "appraisal_reasons": b["appraisal_reasons"],
        "replacement_cost": b["replacement_cost"], "used_value": b["used_value"],
    }


async def price_items(sweep, items: list[dict], country: str, currency: str) -> list[dict]:
    async def one(n, it):
        out = {"id": f"I{n:03d}", "category": it["category"], "description": it["description"],
               "material": it["material"], "brand_model": it["brand_model"], "frame_ref": it["frame_ref"],
               "box_2d": it["box_2d"], "dimensions_cm": it["dimensions_cm"], "scale_method": it["scale_method"],
               "art_id": it.get("art_id", ""),
               "confidence": 0.7 if it["brand_model"] else 0.5}
        if it["category"] in ("art", "portrait") and not said_is_print(sweep, it):
            out.update(status="needs_appraisal", appraisal_reason="art defaults to appraisal unless the policyholder says it is a print",
                       replacement_cost={"low": None, "high": None, "source": "", "url": "", "retrieved_at": ""})
            return out
        out["status"], out["replacement_cost"] = await valuation.price_item(sweep, it, country, currency)
        return out

    return await vision.gather_limited([one(n + 1, it) for n, it in enumerate(items)], limit=6)


def said_is_print(sweep, item: dict) -> bool:
    """True if the user said this piece is a print: matched by its ART id, else by description."""
    for s in sweep.statements:
        if s["kind"] != "is_print":
            continue
        if item.get("art_id") and item["art_id"].lower() in s["about"].lower():
            return True
        if fuzz.token_set_ratio(s["about"].lower(), item["description"].lower()) >= 60:
            return True
    return False


# ---------- run everything ----------

async def finish_sweep(sweep, country: str | None = None, currency: str | None = None) -> dict:
    country, currency = country or sweep.country, currency or sweep.currency
    sweep.save_state()   # so the whole pipeline can be re-run offline from disk
    with timed(sweep, "books_consolidate"):
        await consolidate_all_shelves(sweep)
    with timed(sweep, "books_identify"):
        books = await identify_books(sweep)
    sweep.log("books_identified", {"books": books})
    with timed(sweep, "items_consolidate"):
        items = await consolidate_items(sweep)
    with timed(sweep, "measure"):
        measure_books(sweep, books)
        measure_items(sweep, items)
        room = measure_room(sweep, items)
    sweep.log("measured", {"room": room, "items": items})
    with timed(sweep, "price"):
        priced_books = await price_books(sweep, books, country, currency)
        priced_items = await price_items(sweep, items, country, currency)
    with timed(sweep, "packet"):
        packet = build_packet(sweep, room, [to_packet_book(b, n + 1) for n, b in enumerate(priced_books)],
                              priced_items, country, currency)
    write_packet(sweep, packet, suffix="" if country == sweep.country else f"_{country}")
    return packet


def build_packet(sweep, room, books, items, country, currency) -> dict:
    return {
        "sweep": {"id": sweep.id, "captured_at": datetime.fromtimestamp(sweep.started_at, timezone.utc).isoformat(timespec="seconds"),
                  "device": sweep.device, "duration_s": sweep.elapsed(), "country": country, "currency": currency,
                  "frames_saved": len(sweep.frames), "frames_scanned": len(sweep.scans),
                  "frames_dir": f"sweeps/{sweep.id}/frames/",
                  "skipped_segments": sweep.skipped_segments, "statements": sweep.statements},
        "room": room,
        "books": books,
        "items": items,
        "totals": totals(books, items),
        "review_queue": review_queue(books, items, room),
        "metrics": {"vision_provider": config.VISION_PROVIDER, "stage_seconds": sweep.stage_seconds, "usage": sweep.usage, "cost_usd": cost_usd(sweep)},
    }


def cost_usd(sweep) -> dict:
    u = sweep.usage
    parts = {
        "vision": (u["vision_in"] * config.PRICE_VISION_IN + u["vision_out"] * config.PRICE_VISION_OUT) / 1e6,
        "claude_vision": (u["claude_in"] * config.PRICE_CLAUDE_IN + u["claude_out"] * config.PRICE_CLAUDE_OUT) / 1e6,
        "live": (u["live_in"] * config.PRICE_LIVE_IN + u["live_out"] * config.PRICE_LIVE_OUT) / 1e6,
        "serpapi": u["serpapi"] * config.PRICE_SERPAPI_CALL,
        "ebay": 0.0,
    }
    parts = {k: round(v, 4) for k, v in parts.items()}
    parts["total"] = round(sum(parts.values()), 4)
    return parts


def write_packet(sweep, packet: dict, suffix: str = "") -> None:
    (sweep.dir / f"claim_packet{suffix}.json").write_text(json.dumps(packet, indent=2), encoding="utf-8")
    (sweep.dir / f"report{suffix}.html").write_text(report.render(packet), encoding="utf-8")
