"""Turning pixel boxes into centimetres. Pure functions, no I/O, fully unit-tested.

Where metric scale comes from, in order of preference, per frame:
  1. A reference object of known size in the same frame (door, paper, card, outlet cover).
  2. Books in the same frame whose exact edition we matched in the catalogue and whose
     published height we know (Open Library physical_dimensions).
  3. The median scale of other frames of the same shelf (same distance, same unit).
  4. Standard book format: the shelf's median spine is assumed to be STANDARD_BOOK_HEIGHT_CM tall.
If none of these exist, the dimension is left empty and the line goes to review.
The model never estimates a size.
"""

from statistics import median

from app import config


def box_px(box: list[int], width: int, height: int) -> tuple[float, float]:
    """Gemini box [ymin, xmin, ymax, xmax] on a 0-1000 grid -> (height_px, width_px) in the real image."""
    ymin, xmin, ymax, xmax = box
    return (ymax - ymin) / 1000 * height, (xmax - xmin) / 1000 * width


def reference_size_cm(kind: str, door_height_cm: float) -> float:
    """Real length of the reference's long side."""
    return door_height_cm if kind == "door" else max(config.REFERENCE_OBJECTS_CM[kind])


def box_area(box: list[int]) -> int:
    ymin, xmin, ymax, xmax = box
    return (ymax - ymin) * (xmax - xmin)


def scale_from_references(refs: list[dict], img_w: int, img_h: int, door_height_cm: float) -> tuple[float | None, str]:
    """Pixels per cm from known objects in one frame. Returns (scale, method) or (None, '').

    Only the largest kind of reference in view is used: a few pixels of box error is 1% of a door
    but 10% of an outlet plate, and a big object is also harder to mistake for something else.
    """
    usable = [r for r in refs if r.get("fully_visible") and r.get("facing_camera")]   # hidden/angled = wrong size
    if not usable:
        return None, ""
    biggest = max(reference_size_cm(r["kind"], door_height_cm) for r in usable)
    estimates = []
    for r in usable:
        size_cm = reference_size_cm(r["kind"], door_height_cm)
        if size_cm == biggest:
            h_px, w_px = box_px(r["box_2d"], img_w, img_h)
            estimates.append((h_px if r["kind"] == "door" else max(h_px, w_px)) / size_cm)   # long side to long side
    kind = next(r["kind"] for r in usable if reference_size_cm(r["kind"], door_height_cm) == biggest)
    return median(estimates), f"reference:{kind}"


def spine_px(spine: dict, img_w: int, img_h: int) -> tuple[float, float]:
    """(spine length px, thickness px). A book lying flat has its spine running horizontally."""
    h_px, w_px = box_px(spine["box_2d"], img_w, img_h)
    return (h_px, w_px) if spine["orientation"] == "vertical" else (w_px, h_px)


def scale_from_known_books(books: list[dict], img_w: int, img_h: int) -> float | None:
    """Pixels per cm from books in this frame whose catalogue height we know."""
    estimates = []
    for b in books:
        if b.get("catalog_height_cm"):
            length_px, _ = spine_px(b, img_w, img_h)
            estimates.append(length_px / b["catalog_height_cm"])
    return median(estimates) if estimates else None


def scale_from_standard_format(books: list[dict], img_w: int, img_h: int, standard_cm: float) -> float | None:
    """Last resort: assume the median spine on this shelf is a standard-height book."""
    lengths = [spine_px(b, img_w, img_h)[0] for b in books]
    return median(lengths) / standard_cm if lengths else None


def spine_cm(spine: dict, px_per_cm: float, img_w: int, img_h: int) -> tuple[float, float]:
    length_px, thick_px = spine_px(spine, img_w, img_h)
    return round(length_px / px_per_cm, 1), round(thick_px / px_per_cm, 1)


def item_cm(box: list[int], px_per_cm: float, img_w: int, img_h: int) -> dict:
    """Visible width and height of an item. Depth cannot be seen from the front, so it is None (unknown)."""
    h_px, w_px = box_px(box, img_w, img_h)
    return {"w": round(w_px / px_per_cm, 1), "h": round(h_px / px_per_cm, 1), "d": None}


def wall_cm(wall_box: list[int], px_per_cm: float, img_w: int, img_h: int) -> tuple[float, float]:
    """(wall width cm, wall height cm) from a straight-on full-wall frame."""
    h_px, w_px = box_px(wall_box, img_w, img_h)
    return w_px / px_per_cm, h_px / px_per_cm


def plausible_wall_box(box: list[int] | None) -> bool:
    """Reject boxes the model sometimes returns that cannot be a wall: empty, tiny, or exactly the whole frame."""
    if not box:
        return False
    ymin, xmin, ymax, xmax = box
    return ymax - ymin >= 100 and xmax - xmin >= 100 and box != [0, 0, 1000, 1000]


def walls_from_views(views: list[dict], door_height_cm: float) -> tuple[dict[int, tuple[float, float]], float | None]:
    """Room walls from whole-wall frames. Each view: {"wall": n, "wall_box": box, "door_box": box or None,
    "w": img_w, "h": img_h}.

    1. Ceiling height: in every view with a door, ceiling = wall height in px / (door height in px / door cm).
       The ceiling is the same height everywhere, so the median over all views rejects bad boxes.
    2. Each wall's width = (its median width:height ratio over its views) x the ceiling height.
    Returns ({wall: (width_m, height_m)}, ceiling_m).
    """
    ceilings, ratios = [], {}
    for v in views:
        if not plausible_wall_box(v["wall_box"]):
            continue
        wall_h, wall_w = box_px(v["wall_box"], v["w"], v["h"])
        ratios.setdefault(v["wall"], []).append(wall_w / wall_h)
        if v.get("door_box"):
            door_h, _ = box_px(v["door_box"], v["w"], v["h"])
            if 0 < door_h < wall_h:   # a door taller than its wall is a bad box
                ceilings.append(wall_h / (door_h / door_height_cm) / 100)
    if not ceilings:
        return {}, None
    ceiling = median(ceilings)
    return {n: (median(r) * ceiling, ceiling) for n, r in ratios.items()}, ceiling


def room_from_walls(walls: dict[int, tuple[float, float]]) -> dict:
    """walls: {wall number 1-4 in walking order: (width_m, height_m)}.

    Walls 1 and 3 face each other, as do 2 and 4. Length = mean of 1 & 3, width = mean of 2 & 4.
    If opposite walls disagree by more than 15% the room is probably not a rectangle (or one wall
    was measured badly); we still report the mean but flag it and lower confidence.
    """
    def pair(a, b):
        vals = [walls[k][0] for k in (a, b) if k in walls]
        if not vals:
            return None, False
        mismatch = len(vals) == 2 and abs(vals[0] - vals[1]) / max(vals) > 0.15
        return sum(vals) / len(vals), mismatch

    length, mis_l = pair(1, 3)
    width, mis_w = pair(2, 4)
    heights = [h for _, h in walls.values()]
    height = median(heights) if heights else None
    notes = []
    if mis_l or mis_w:
        notes.append("opposite walls differ by >15%: room may not be rectangular; used the mean")
    missing = [k for k in (1, 2, 3, 4) if k not in walls]
    if missing:
        notes.append(f"walls {missing} not measured; used the opposite wall")
    if length is None or width is None or height is None:
        return {"length_m": None, "width_m": None, "height_m": None, "floor_area_m2": None, "wall_area_m2": None,
                "confidence": 0, "shape_notes": notes + ["not enough walls measured"]}
    confidence = 0.8 - 0.15 * len(missing) - (0.2 if notes and (mis_l or mis_w) else 0)
    return {
        "length_m": round(length, 2), "width_m": round(width, 2), "height_m": round(height, 2),
        "floor_area_m2": round(length * width, 2),
        "wall_area_m2": round(2 * (length + width) * height, 2),   # gross, openings not subtracted
        "confidence": round(max(confidence, 0.1), 2),
        "shape_notes": notes,
    }


def m2_to_ft2(m2: float) -> float:
    return round(m2 * 10.7639, 1)
