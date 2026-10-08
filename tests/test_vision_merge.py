"""The merge step must only ever return sightings that exist, each at most once."""

import asyncio

from app import vision
from app.sweep import Frame


class FakeSweep:
    def __init__(self, scans):
        self.scans = scans


def test_consolidate_shelf_drops_bad_and_duplicate_picks(monkeypatch, tmp_path):
    frames = [Frame(f"f{i}.jpg@{i}s", tmp_path / f"f{i}.jpg", i, "shelf_A", 100, 0) for i in range(2)]
    spine = {"box_2d": [0, 0, 10, 10], "orientation": "vertical", "legible": True, "title": "Dune",
             "author": "", "publisher": "", "looks_antiquarian": False}
    sweep = FakeSweep({f.ref: {"spines": [spine]} for f in frames})

    async def fake_generate(sweep, parts, schema, prompt):
        return vision.ShelfBooks(books=[
            vision.BookPick(frame=1, spine_index=0, row=1),
            vision.BookPick(frame=1, spine_index=0, row=1),   # duplicate
            vision.BookPick(frame=2, spine_index=5, row=1),   # no such spine
            vision.BookPick(frame=9, spine_index=0, row=1),   # no such frame
            vision.BookPick(frame=2, spine_index=0, row=1),
        ])

    monkeypatch.setattr(vision, "_generate", fake_generate)
    books = asyncio.run(vision.consolidate_shelf(sweep, "shelf_A", frames))
    assert [b["frame_ref"] for b in books] == [frames[0].ref, frames[1].ref]
