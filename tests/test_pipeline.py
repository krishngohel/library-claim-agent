"""Whole post-sweep pipeline on fake frames, with the model and price APIs replaced by stubs."""

import asyncio
import json

import cv2
import numpy as np

from app import catalog, config, pipeline, vision
from app.sweep import Sweep

DOOR = {"box_2d": [100, 600, 913, 900], "kind": "door", "fully_visible": True, "facing_camera": True}


def spine(box, title, legible=True):
    return {"box_2d": box, "orientation": "vertical", "legible": legible, "title": title if legible else "",
            "author": "", "publisher": "", "looks_antiquarian": False}


def make_sweep(tmp_path, monkeypatch) -> Sweep:
    monkeypatch.setattr(config, "SWEEPS_DIR", tmp_path)
    sweep = Sweep()
    jpeg = cv2.imencode(".jpg", np.zeros((1000, 1000, 3), np.uint8))[1].tobytes()
    for segment in ("wall_1", "wall_2", "wall_3", "wall_4", "shelf_A"):
        sweep.current_segment = segment
        f = sweep.save_frame(jpeg, 200, 0)
        if segment.startswith("wall"):
            width = 400 if segment in ("wall_1", "wall_3") else 300   # 400 px / 4 px per cm = 100 cm
            sweep.scans[f.ref] = {"spines": [], "items": [], "references": [DOOR], "capture_issues": [],
                                  "wall_box": [0, 0, 975, width]}
        else:
            sweep.scans[f.ref] = {
                "spines": [spine([0, 0, 80, 10], "The Hobbit"), spine([0, 10, 80, 20], "", legible=False)],
                "items": [{"box_2d": [0, 0, 400, 200], "category": "shelving", "description": "bookcase",
                           "material": "wood", "brand_model": ""}],
                "references": [{"box_2d": [0, 0, 279, 216], "kind": "us_letter_paper", "fully_visible": True,
                                "facing_camera": True}],
                "capture_issues": [], "wall_box": None}
    return sweep


def test_finish_sweep_writes_contract_packet(tmp_path, monkeypatch):
    sweep = make_sweep(tmp_path, monkeypatch)

    async def consolidate_shelf(sw, shelf, frames):
        f = frames[0]
        return [{**s, "frame_ref": f.ref, "row": 1} for s in sw.scans[f.ref]["spines"]]

    async def consolidate_items(sw, frames):   # called once per segment
        with_items = [fr for fr in frames if sw.scans[fr.ref]["items"]]
        return [{**sw.scans[f.ref]["items"][0], "frame_ref": f.ref} for f in with_items[:1]]

    async def identify(title, author, publisher):
        return {"title": title, "author": "J.R.R. Tolkien", "edition": "", "isbn": "", "catalog_url": "https://ol",
                "catalog_height_cm": None, "first_publish_year": 1937, "match_score": 95, "resolved": True}

    async def no_offers(*a, **k):
        return []

    monkeypatch.setattr(vision, "consolidate_shelf", consolidate_shelf)
    monkeypatch.setattr(vision, "consolidate_items", consolidate_items)
    monkeypatch.setattr(catalog, "identify", identify)
    monkeypatch.setattr(pipeline.valuation.prices, "shopping_offers", no_offers)
    monkeypatch.setattr(pipeline.valuation.prices, "ebay_offers", no_offers)

    packet = asyncio.run(pipeline.finish_sweep(sweep))

    on_disk = json.loads((sweep.dir / "claim_packet.json").read_text())
    assert on_disk == packet
    assert set(packet) >= {"sweep", "room", "books", "items", "totals", "review_queue"}
    assert (sweep.dir / "report.html").exists()

    assert packet["items"][0]["dimensions_cm"]["d"] is None   # depth unknown -> empty, not 0
    # Room: door is 813 px tall = 203.2 cm -> 4 px/cm. Walls 400 px and 300 px wide -> 1.0 m x 0.75 m.
    assert packet["room"]["length_m"] == 1.0
    assert packet["room"]["width_m"] == 0.75
    assert packet["room"]["height_m"] == 2.44

    hobbit, unknown = packet["books"]
    assert hobbit["status"] == "identified" and hobbit["title"] == "The Hobbit"
    # Paper: 279 px long side = 27.94 cm -> ~10 px/cm. Spine box 80 x 10 px -> 8.0 x 1.0 cm.
    assert (hobbit["spine_height_cm"], hobbit["spine_thickness_cm"]) == (8.0, 1.0)
    assert unknown["status"] == "unidentified" and unknown["title"] == ""

    # No price sources -> no prices, nothing invented, everything excluded and queued.
    assert hobbit["replacement_cost"]["amount"] is None
    assert packet["totals"]["books_replacement_cost"] == 0
    assert packet["totals"]["excluded_from_totals"] == 3
    assert any(r["ref_id"] == hobbit["id"] and "no replacement price" in r["reason"] for r in packet["review_queue"])

    # Every frame_ref points to a file on disk.
    for line in packet["books"] + packet["items"]:
        assert (sweep.dir / "frames" / line["frame_ref"].split("@")[0]).exists()

    # The saved state can be reloaded for offline replay.
    assert len(Sweep.load_state(sweep.id).frames) == len(sweep.frames)
