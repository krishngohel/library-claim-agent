from app.packet import review_queue, totals


def book(id, status="identified", repl=10.0, used=5.0, thick=2.0, conf=0.95, converted=False):
    return {"id": id, "status": status, "id_confidence": conf, "spine_height_cm": 20.0 if thick else 0,
            "spine_thickness_cm": thick, "scale_method": "reference:door", "appraisal_reasons": ["signed"],
            "replacement_cost": {"amount": repl, "converted": converted}, "used_value": {"amount": used}}


def item(id, status="priced", low=100.0, high=100.0):
    return {"id": id, "status": status, "dimensions_cm": {"w": 50, "h": 50, "d": 0},
            "replacement_cost": {"low": low, "high": high}}


ROOM = {"confidence": 0.8, "shape_notes": []}


def test_totals_add_up_from_lines():
    books = [book("B1"), book("B2", repl=20.0, used=None)]
    items = [item("I1"), item("I2", status="range", low=50.0, high=80.0)]
    t = totals(books, items)
    assert t["books_replacement_cost"] == 30.0
    assert t["books_used_value"] == 5.0
    assert t["items_replacement_cost_low"] == 150.0
    assert t["items_replacement_cost_high"] == 180.0
    assert t["shelf_run_m"] == 0.04


def test_appraisal_and_unpriced_lines_are_excluded():
    books = [book("B1"), book("B2", status="needs_appraisal", repl=500.0), book("B3", repl=None)]
    items = [item("I1", status="needs_appraisal", low=None, high=None)]
    t = totals(books, items)
    assert t["books_replacement_cost"] == 10.0
    assert t["excluded_from_totals"] == 3
    assert set(t["excluded_ids"]) == {"B2", "B3", "I1"}


def test_review_queue_reasons():
    books = [book("B1", status="unidentified", repl=None, used=None, thick=0),
             book("B2", conf=0.6), book("B3", converted=True)]
    reasons = {(r["ref_id"], r["reason"].split(";")[0].split(" (")[0]) for r in review_queue(books, [], ROOM)}
    assert ("B1", "spine not legible in any frame") in reasons
    assert ("B1", "no metric scale available for this frame") in reasons
    assert ("B2", "weak catalogue match") in reasons
    queue = review_queue(books, [], ROOM)
    assert any(r["ref_id"] == "B3" and "converted" in r["reason"] for r in queue)


def test_low_confidence_room_goes_to_review():
    queue = review_queue([], [], {"confidence": 0.3, "shape_notes": ["walls [3, 4] not measured"]})
    assert queue[0]["ref_id"] == "room"


def test_building_parts_are_not_contents():
    from app.pipeline import is_contents
    assert not is_contents({"category": "other", "description": "brown wooden interior door with knob"})
    assert not is_contents({"category": "other", "description": "white outlet cover"})
    assert is_contents({"category": "lamp", "description": "floor lamp by the window"})
    assert is_contents({"category": "electronics", "description": "Sonos speaker"})


def test_identical_pictures_on_opposite_walls_stay_separate():
    from app.pipeline import merge_across_segments
    art = {"category": "art", "description": "framed landscape print", "box_2d": [0, 0, 100, 100]}
    items = [{**art, "segment": "wall_1"}, {**art, "segment": "wall_3"}]
    assert len(merge_across_segments(items)) == 2


def test_bookcase_seen_in_wall_view_and_closeup_is_one_object():
    from app.pipeline import merge_across_segments
    wide = {"category": "shelving", "description": "white five-shelf bookcase", "segment": "wall_2", "box_2d": [0, 0, 300, 200]}
    close = {"category": "shelving", "description": "white bookcase", "segment": "shelf_A", "box_2d": [0, 0, 900, 900]}
    other = {**close, "segment": "shelf_B"}
    merged = merge_across_segments([wide, close, other])
    assert len(merged) == 2                       # shelf_A and shelf_B are different units
    assert merged[0]["segment"] == "wall_2"       # the whole-wall view is kept: it shows the whole bookcase


def test_adjacent_walls_merge_only_corner_objects():
    from app.pipeline import merge_across_segments
    lamp = {"category": "lamp", "description": "black floor lamp"}
    corner = [{**lamp, "segment": "wall_1", "box_2d": [0, 900, 500, 990]}, {**lamp, "segment": "wall_2", "box_2d": [0, 10, 500, 90]}]
    middle = [{**lamp, "segment": "wall_1", "box_2d": [0, 400, 500, 500]}, {**lamp, "segment": "wall_2", "box_2d": [0, 400, 500, 500]}]
    assert len(merge_across_segments(corner)) == 1
    assert len(merge_across_segments(middle)) == 2


def test_separately_asked_art_never_merges():
    from app.pipeline import merge_across_segments
    art = {"category": "art", "description": "framed print", "segment": "wall_1", "box_2d": [0, 0, 100, 100]}
    assert len(merge_across_segments([{**art, "art_id": "ART1"}, {**art, "art_id": "ART2"}])) == 2
