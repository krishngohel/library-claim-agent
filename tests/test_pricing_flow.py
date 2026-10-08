"""Full pipeline with fake price listings: prices, conversion, appraisal rules and totals end to end."""

import asyncio

import cv2
import numpy as np

from app import catalog, config, pipeline, vision
from app.sweep import Sweep
from tests.contract import problems

CARD = {"box_2d": [0, 0, 54, 86], "kind": "credit_card", "fully_visible": True, "facing_camera": True}


def spine(x, title):
    return {"box_2d": [0, x, 220, x + 40], "orientation": "vertical", "legible": True, "title": title,
            "author": "", "publisher": "", "looks_antiquarian": False}


def listing(title, amount, currency, used=False, source="test shop"):
    return {"title": title, "amount": amount, "currency": currency, "source": source, "used": used,
            "url": f"https://shop.example/{title.replace(' ', '-')}/{amount}", "retrieved_at": "2026-10-08T01:00:00+00:00"}


def test_prices_flow_into_packet(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SWEEPS_DIR", tmp_path)
    sweep = Sweep()
    jpeg = cv2.imencode(".jpg", np.zeros((1000, 1000, 3), np.uint8))[1].tobytes()
    sweep.current_segment = "shelf_A"
    f = sweep.save_frame(jpeg, 200, 0)
    sweep.scans[f.ref] = {
        "spines": [spine(0, "Dune"), spine(50, "Rare Atlas"), spine(100, "British Only")],
        "items": [{"box_2d": [0, 0, 300, 300], "category": "electronics", "description": "speaker",
                   "material": "plastic", "brand_model": "Sonos One"},
                  {"box_2d": [400, 0, 600, 300], "category": "art", "description": "framed poster",
                   "material": "paper", "brand_model": "", "art_id": "ART1"}],
        "references": [CARD], "capture_issues": [], "wall_box": None}
    sweep.statements.append({"kind": "is_print", "about": "ART1", "quote": "it's a print"})

    async def consolidate_shelf(sw, shelf, frames):
        return [{**s, "frame_ref": frames[0].ref, "row": 1} for s in sw.scans[frames[0].ref]["spines"]]

    async def consolidate_items(sw, frames):
        return [{**it, "frame_ref": fr.ref} for fr in frames for it in sw.scans[fr.ref]["items"]]

    async def identify(title, author, publisher):
        return {"title": title, "author": "Someone", "edition": "", "isbn": "", "catalog_url": "https://ol",
                "catalog_height_cm": None, "first_publish_year": 2000, "match_score": 95, "resolved": True}

    async def shopping(sw, query, country):
        if "Dune" in query and country == "US":
            return [listing("Dune", 10.0, "USD"), listing("Dune", 12.0, "USD"), listing("Dune", 30.0, "USD")]
        if "Rare Atlas" in query and country == "US":
            return [listing("Rare Atlas", 900.0, "USD")]
        if "British Only" in query and country == "GB":
            return [listing("British Only", 8.0, "GBP")]
        if "Sonos" in query:
            return [listing("Sonos One speaker", 199.0, "USD"), listing("Sonos One (2nd gen)", 219.0, "USD")]
        if "poster" in query:
            return [listing("framed poster", 20.0, "USD"), listing("framed poster", 40.0, "USD"),
                    listing("framed poster", 60.0, "USD")]
        return []

    async def ebay(sw, query, country, condition):
        if "Dune" in query and condition == "USED" and country == "US":
            return [listing("Dune paperback", 4.0, "USD", used=True, source="eBay EBAY_US (used)")]
        return []

    async def fx(a, b):
        return {"rate": 1.25, "date": "2026-10-07", "url": "https://fx"}

    monkeypatch.setattr(vision, "consolidate_shelf", consolidate_shelf)
    monkeypatch.setattr(vision, "consolidate_items", consolidate_items)
    monkeypatch.setattr(catalog, "identify", identify)
    monkeypatch.setattr(pipeline.valuation.prices, "shopping_offers", shopping)
    monkeypatch.setattr(pipeline.valuation.prices, "ebay_offers", ebay)
    monkeypatch.setattr(pipeline.valuation.prices, "fx_rate", fx)

    packet = asyncio.run(pipeline.finish_sweep(sweep))
    books = {b["title"]: b for b in packet["books"]}
    items = {i["description"]: i for i in packet["items"]}

    dune = books["Dune"]
    assert dune["replacement_cost"]["amount"] == 12.0                 # median real listing, not an average
    assert dune["replacement_cost"]["url"].endswith("/12.0")          # traceable to that listing
    assert dune["used_value"]["amount"] == 4.0
    assert abs(dune["spine_height_cm"] - 21.9) < 0.05 and dune["spine_thickness_cm"] == 4.0   # card: 86 px = 8.56 cm

    atlas = books["Rare Atlas"]
    assert atlas["status"] == "needs_appraisal"                       # over the 150 threshold
    assert atlas["replacement_cost"]["amount"] is None                # ...so it is not auto-priced
    assert atlas["listings_seen"]                                     # but the evidence is kept for the appraiser

    british = books["British Only"]
    assert british["replacement_cost"]["amount"] == 10.0              # 8 GBP x 1.25
    assert british["replacement_cost"]["converted"] is True

    assert items["speaker"]["status"] == "priced"
    assert items["speaker"]["replacement_cost"]["low"] == 199.0
    assert items["framed poster"]["status"] == "range"                # user said it is a print
    assert (items["framed poster"]["replacement_cost"]["low"], items["framed poster"]["replacement_cost"]["high"]) == (20.0, 60.0)

    t = packet["totals"]
    assert t["books_replacement_cost"] == 22.0                        # Dune 12 + British 10; atlas excluded
    assert t["books_used_value"] == 4.0
    assert t["items_replacement_cost_low"] == 219.0 and t["items_replacement_cost_high"] == 259.0
    assert atlas["id"] in t["excluded_ids"]
    assert problems(packet, sweep.dir) == []
