# Failure log

> Fill this in from your own recorded sweep and `eval/evaluate.py` results. Every number here must
> come from `sweeps/<id>/results.md` and `claim_packet.json` → `metrics`. Do not estimate.

## Three worst errors

### 1. <error name>
- **What happened:** (measured: e.g. "book count 71 vs 64 true, +10.9%")
- **Root cause:** (which stage: look at `stages.jsonl` and the frame_refs involved)
- **What I tried / measured fix:** (before → after, re-run with `python -m scripts.replay <id>`)
- **What I'd change with two more weeks:**

### 2. <error name>
...

### 3. <error name>
...

## Cost per sweep

From `claim_packet.json` → `metrics.cost_usd` (token counts come from the API's usage metadata;
unit prices are set in `.env` / `app/config.py`. Check them against the provider pricing pages).

| Part | USD |
| --- | --- |
| Gemini Live | |
| Gemini Flash (vision) | |
| SerpAPI calls | |
| eBay | 0 (free tier) |
| **Total** | |

## Latency per stage

From `metrics.stage_seconds`.

| Stage | Seconds |
| --- | --- |
| books_consolidate | |
| books_identify | |
| items_consolidate | |
| measure | |
| price | |
| packet | |
| **Sweep end → packet** | |

## Found during development: synthetic smoke-test room (`scripts/smoke_test.py`)

These come from the synthetic room: 14 books on 2 bookcases, 4 walls, 4 pictures, scored with
`eval/evaluate.py` against `eval/smoke_ground_truth/`. They are real bugs, found and fixed with measured
before/after numbers, but synthetic frames are easier than a real room. They do not replace the real-room
log above.

### A. Every shelf frame thrown away, 0 books logged
- **What happened:** book count 0 vs 14. The live inventory stayed empty during shelf passes.
- **Root cause:** `quality.py` treated any pixel ≥ 250 as glare. The white sheet of paper placed as a
  scale reference covered more than 4% of the frame, so every shelf frame failed the glare check and was
  never scanned. The fix for scale broke reading.
- **Fix:** glare now counts only clipped pixels (≥ 254) over 5%. Book count went from 0/14 to 14/14.
- **Also fixed:** the end-of-sweep gap check now refuses to finish when a shelf has no usable frames.
  Before, it built an empty packet.

### B. Non-book items silently dropped
- **What happened:** 0 items in the packet, although the live scans had detected the pictures.
- **Root cause:** the merge prompt referred to frames by file name (`f0026.jpg@38.5s`). The model
  answered with shortened names (`f0026.jpg`), so every pick failed validation and was correctly thrown
  away. Validation worked, but the identifiers were fragile.
- **Fix:** frames are numbered 1..N in merge prompts and mapped back in code. Items went from 0/2 to found.
  Then four identical pictures on four walls were merged into one. Merging moved to per-wall-or-shelf model
  calls, plus a code rule that only merges objects that could physically be the same. Items went from
  2/5 to 5/5 (`test_identical_pictures_on_opposite_walls_stay_separate`).

### C. Room size swung 9% to 52% between runs on the same kind of frames
- **What happened:** floor area 10.31 vs 21.72 m² (52.5% error) on one run, 27.0% on another.
- **Root cause 1:** per frame, the median of all reference objects was used, so a picture frame mislabelled
  `us_letter_paper` outvoted the door. **Fix:** use only the largest kind of reference in view.
- **Root cause 2:** the model's boxes are noisy from frame to frame. One door's bottom edge came back as
  642, 800, 889 and 1000, and some wall boxes were the whole image or all zeros. Any single frame could
  wreck a wall. **Fix** (`scale.walls_from_views`): reject impossible boxes, take ceiling height as the
  median over all door views (it is the same everywhere in a room), and make each wall's width its own
  width:height ratio × that ceiling.
- **Measured:** floor area error went from 27.0% to **2.1%** on that sweep, and to **6.6%** on a second
  saved sweep (bar 10%). Re-running gives the same result.
- **Still failing:** wall area is 17.8–21.5% off (bar 15%). The model puts the wall's top edge at the top of
  the image instead of the ceiling line, about 7% too tall. In each wall's width that error cancels: width =
  (wall px ÷ door px) × door cm, so the wall height drops out. Ceiling height keeps the error, and wall area
  = perimeter × ceiling height. With two more weeks: find the ceiling and floor lines with an edge detector
  (Hough lines) instead of the model's box.

### D. Requirement checks: bugs found by the contract and pricing-flow tests
- **A book flagged for appraisal still carried its price.** Books listed above `APPRAISAL_THRESHOLD` were
  marked `needs_appraisal` but kept the amount, which breaks "do not auto-price these". Found by
  `tests/test_pricing_flow.py`. Now the amount is blank and the listings go in `listings_seen` as evidence.
- **Re-filming a shelf kept the old merge.** If the agent asked for a re-capture and the user went back to a
  shelf, the earlier result was reused. Entering a shelf now discards its old merge.
- **Long shelves could lose books.** Only 10 frames per shelf went to the merge. A 60 s pass makes about
  24 scans, so books between the chosen frames were never seen. The cap is now 24 (`SHELF_MERGE_MAX_FRAMES`).
- **Shelved wall area measured from close-ups.** A close-up shows part of a bookcase, so its area was too
  small. Merging now keeps the whole-wall sighting for measurement, and objects in a wall view get scale
  from the wall's own floor-to-ceiling height.

### Smoke-test cost and latency (Claude mode, measured)
- Total model cost per 3-minute sweep: **$0.027** (Claude Haiku 5.5: agent turns + about 20 frame scans + merges)
- Sweep end to packet: **7–13 s** (books_consolidate 3.6 s, books_identify 0.1 s with cache / 2.4 s cold,
  items_consolidate 4.5–9 s, measure 0.1 s, price 0 s with no price keys)

## Known risks to check first (predicted, not yet measured)

- **Duplicate or missed books across frames.** Shelf merging relies on the vision model matching
  sightings across overlapping frames. If the count is off, look at which frames `close_shelf` used
  (`stages.jsonl`).
- **Scale from a door applied to objects in front of the wall.** About 10% error at 3 m. Paper on the
  shelf fixes it.
- **Room walls filmed at an angle.** Wall width assumes a straight-on view. Check `room.wall_frames`.
- **Shopping listings for the wrong format** (hardcover vs paperback, box sets). Only titles are matched.
- **Live session limits.** Long sweeps depend on context-window compression staying enabled.
