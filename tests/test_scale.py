import pytest

from app import scale
from app.catalog import parse_height_cm

W, H = 1000, 1000   # a 1000x1000 image makes Gemini's 0-1000 boxes equal to pixels


def test_box_px_converts_normalised_box_to_pixels():
    assert scale.box_px([100, 200, 300, 600], 2000, 1000) == (200.0, 800.0)


def test_scale_from_door_uses_door_height():
    door = {"box_2d": [0, 0, 812, 300], "kind": "door", "fully_visible": True, "facing_camera": True}
    px, method = scale.scale_from_references([door], W, H, door_height_cm=203.2)
    assert px == pytest.approx(812 / 203.2)
    assert method == "reference:door"


def test_paper_is_orientation_free():
    # Letter paper lying sideways: long side is horizontal
    paper = {"box_2d": [0, 0, 216, 279], "kind": "us_letter_paper", "fully_visible": True, "facing_camera": True}
    px, _ = scale.scale_from_references([paper], W, H, 203.2)
    assert px == pytest.approx(279 / 27.94)


def test_partially_visible_reference_is_ignored():
    door = {"box_2d": [0, 0, 500, 300], "kind": "door", "fully_visible": False, "facing_camera": True}
    assert scale.scale_from_references([door], W, H, 203.2) == (None, "")


def test_flat_book_swaps_length_and_thickness():
    flat = {"box_2d": [500, 100, 530, 300], "orientation": "flat"}
    assert scale.spine_cm(flat, 10, W, H) == (20.0, 3.0)
    upright = {"box_2d": [100, 500, 300, 530], "orientation": "vertical"}
    assert scale.spine_cm(upright, 10, W, H) == (20.0, 3.0)


def test_scale_from_known_books():
    books = [{"box_2d": [0, 0, 200, 30], "orientation": "vertical", "catalog_height_cm": 20.0}]
    assert scale.scale_from_known_books(books, W, H) == pytest.approx(10.0)


def test_rectangular_room():
    room = scale.room_from_walls({1: (4.0, 2.4), 2: (3.0, 2.4), 3: (4.0, 2.4), 4: (3.0, 2.4)})
    assert room["floor_area_m2"] == 12.0
    assert room["wall_area_m2"] == pytest.approx(2 * 7 * 2.4)
    assert room["shape_notes"] == []


def test_mismatched_walls_are_flagged():
    room = scale.room_from_walls({1: (4.0, 2.4), 2: (3.0, 2.4), 3: (5.0, 2.4), 4: (3.0, 2.4)})
    assert room["length_m"] == 4.5
    assert any("not be rectangular" in n for n in room["shape_notes"])


def test_missing_wall_uses_opposite():
    room = scale.room_from_walls({1: (4.0, 2.4), 2: (3.0, 2.4)})
    assert room["floor_area_m2"] == 12.0
    assert room["confidence"] < 0.8


def test_too_few_walls_gives_zero_confidence():
    assert scale.room_from_walls({1: (4.0, 2.4)})["confidence"] == 0


@pytest.mark.parametrize("text,expected", [
    ("20 x 13 x 2 centimeters", 20.0),
    ("8 x 5.25 x 1 inches", 20.3),
    ("", None),
])
def test_parse_height(text, expected):
    assert parse_height_cm(text) == expected


def test_standard_format_scale_uses_median_spine():
    books = [{"box_2d": [0, 0, h, 30], "orientation": "vertical"} for h in (200, 230, 260)]
    assert scale.scale_from_standard_format(books, W, H, 23.0) == pytest.approx(10.0)


def test_largest_reference_wins_over_small_or_mistaken_ones():
    door = {"box_2d": [0, 0, 812, 300], "kind": "door", "fully_visible": True, "facing_camera": True}
    outlet = {"box_2d": [0, 0, 300, 100], "kind": "us_outlet_cover", "fully_visible": True, "facing_camera": True}
    px, method = scale.scale_from_references([outlet, door], W, H, 203.2)
    assert px == pytest.approx(812 / 203.2) and method == "reference:door"


def test_walls_from_views_rejects_bad_boxes_with_medians():
    # True room: ceiling 2.54 m. Door 480 px tall on a 600 px wall (door 203.2 cm).
    good = {"wall_box": [0, 0, 600, 1100], "door_box": [120, 0, 600, 200], "w": 1000, "h": 1000}   # door 480x200 px
    views = [{**good, "wall": 1}, {**good, "wall": 1},
             {**good, "wall": 1, "door_box": [120, 0, 400, 200]},          # bad door box: 280x200 px is not door-shaped
             {**good, "wall": 2, "wall_box": [0, 0, 1000, 1000]},          # full-frame box: rejected
             {**good, "wall": 2, "wall_box": [0, 0, 0, 0]},                # empty box: rejected
             {**good, "wall": 2, "wall_box": [0, 0, 600, 900], "door_box": None}]
    walls, ceiling, spread = scale.walls_from_views(views, 203.2)
    assert ceiling == pytest.approx(2.54)
    assert walls[1][0] == pytest.approx(2.54 * 1100 / 600)
    assert walls[2][0] == pytest.approx(2.54 * 900 / 600)


def test_no_door_means_no_room_scale():
    view = {"wall": 1, "wall_box": [0, 0, 600, 1100], "door_box": None, "w": 1000, "h": 1000}
    assert scale.walls_from_views([view], 203.2) == ({}, None, 0.0)


def test_door_shape_check():
    assert scale.plausible_door([0, 0, 480, 200], 1000, 1000)        # 2.4 : 1, a real door
    assert not scale.plausible_door([0, 0, 300, 220], 1000, 1000)    # 1.4 : 1, not a door
