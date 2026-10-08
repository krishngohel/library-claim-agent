"""Score a claim packet against hand-collected ground truth, one line per pass bar.

    python -m eval.evaluate sweeps/<id>/claim_packet.json eval/ground_truth

Ground-truth CSVs (templates in eval/ground_truth_template/):
  books.csv   shelf,position,title,author,legible     every book in the room, legible = yes/no
  spines.csv  title,height_cm,thickness_cm            20 hand-measured spines
  prices.csv  title,replacement,used,source_url       15 hand-checked prices, same source type as the system
  items.csv   category,description                    every non-book item
  room.csv    length_m,width_m,height_m               tape measure
Writes results.md next to the packet. Every book and shelf counts; nothing is filtered to the good frames.
"""

import csv
import json
import sys
from pathlib import Path

from rapidfuzz import fuzz


def read(folder: Path, name: str) -> list[dict]:
    with open(folder / name, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def same_title(a: str, b: str) -> bool:
    return bool(a and b) and fuzz.token_set_ratio(a.lower(), b.lower()) >= 85


def find_book(packet_books: list[dict], title: str) -> dict | None:
    return next((b for b in packet_books if same_title(b["title"], title)), None)


def within(measured, truth, tolerance) -> bool:
    return measured not in (None, 0) and abs(measured - truth) / truth <= tolerance


def evaluate(packet: dict, gt: Path) -> list[tuple[str, str, str, bool]]:
    books, rows = packet["books"], []

    true_books = read(gt, "books.csv")
    count_err = abs(len(books) - len(true_books)) / len(true_books)
    rows.append(("Book count", f"{len(books)} vs {len(true_books)} true", f"{count_err:.1%} error (bar 5%)",
                 count_err <= 0.05))

    legible = [b for b in true_books if b["legible"].strip().lower() == "yes"]
    correct = sum(find_book(books, b["title"]) is not None for b in legible)
    true_titles = [b["title"] for b in true_books]
    confident = [b for b in books if b["status"] == "identified" and b["id_confidence"] >= 0.85]
    wrong = [b for b in confident if not any(same_title(b["title"], t) for t in true_titles)]
    id_rate = correct / len(legible) if legible else 0
    wrong_rate = len(wrong) / len(books) if books else 0
    rows.append(("Title identification", f"{correct}/{len(legible)} legible correct; {len(wrong)} confidently wrong",
                 f"{id_rate:.0%} correct (bar 70%), {wrong_rate:.1%} confidently wrong (bar 3%)",
                 id_rate >= 0.70 and wrong_rate <= 0.03))

    spines = read(gt, "spines.csv")
    ok = 0
    for s in spines:
        b = find_book(books, s["title"])
        ok += bool(b and within(b["spine_height_cm"], float(s["height_cm"]), 0.15)
                   and within(b["spine_thickness_cm"], float(s["thickness_cm"]), 0.15))
    rows.append(("Spine dimensions", f"{ok}/{len(spines)} within 15% (height and thickness)",
                 "missing books count as misses", ok / len(spines) >= 0.8 if spines else False))

    prices = read(gt, "prices.csv")
    ok = 0
    for p in prices:
        b = find_book(books, p["title"])
        ok += bool(b and within(b["replacement_cost"]["amount"], float(p["replacement"]), 0.25))
    rows.append(("Book prices", f"{ok}/{len(prices)} replacement within 25%" if prices else "no hand-checked prices",
                 "unpriced counts as a miss", ok / len(prices) >= 0.8 if prices else False))

    true_items, found, used = read(gt, "items.csv"), 0, set()
    for t in true_items:
        for i in packet["items"]:
            if i["id"] not in used and i["category"] == t["category"].strip() and \
                    fuzz.partial_ratio(t["description"].lower(), i["description"].lower()) >= 50:
                used.add(i["id"])
                found += 1
                break
    item_rate = found / len(true_items)
    rows.append(("Non-book items", f"{found}/{len(true_items)} found with correct category",
                 f"{item_rate:.0%} (bar 80%); {len(packet['items']) - found} extra detections", item_rate >= 0.8))

    room_true = read(gt, "room.csv")[0]
    L, W, H = (float(room_true[k]) for k in ("length_m", "width_m", "height_m"))
    floor_true, wall_true = L * W, 2 * (L + W) * H
    room = packet["room"]
    for name, measured, truth, bar in (("Floor area", room["floor_area_m2"], floor_true, 0.10),
                                       ("Wall area", room["wall_area_m2"], wall_true, 0.15)):
        if measured is None:
            rows.append((name, f"not measured vs {truth:.2f} m2", "no estimate", False))
            continue
        err = abs(measured - truth) / truth
        rows.append((name, f"{measured} vs {truth:.2f} m2", f"{err:.1%} error (bar {bar:.0%})", err <= bar))

    seconds = sum(packet["metrics"]["stage_seconds"].values())
    rows.append(("Time to packet", f"{seconds:.0f} s after sweep end", "bar 300 s", seconds < 300))
    return rows


def main():
    packet_path, gt = Path(sys.argv[1]), Path(sys.argv[2])
    rows = evaluate(json.loads(packet_path.read_text(encoding="utf-8")), gt)
    lines = ["| Measure | System vs truth | Detail | Pass |", "|---|---|---|---|"]
    lines += [f"| {m} | {v} | {d} | {'PASS' if ok else 'FAIL'} |" for m, v, d, ok in rows]
    text = "\n".join(lines)
    (packet_path.parent / "results.md").write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
