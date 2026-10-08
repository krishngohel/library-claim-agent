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
- **Root cause 3:** some door boxes have an impossible shape. The same door came back at a height:width
  ratio of 1.4, but real interior doors are 2.2-3.3. **Fix:** door boxes outside 1.9-3.6 are ignored,
  walls are scanned every 1 s instead of 2.5 s for more samples, and when ceiling estimates disagree by
  more than 15% the room is flagged with the measured disagreement.
- **Measured across 5 synthetic sweeps** (all scored, not just the good ones):

  | Sweep | Floor area error (bar 10%) | Wall area error (bar 15%) | Flagged for review |
  |---|---|---|---|
  | 1 | 3.8% | 18.3% | yes (17% disagreement) |
  | 2 | 14.9% | 1.8% | yes (15%) |
  | 3 | 4.9% | 21.1% | yes (23%) |
  | 4 | 1.1% | 8.3% | yes (17%) |
  | 5 (1 s wall scans) | 10.4% | 5.8% | yes (20%) |

  Floor area passes in 3 of 5 runs and wall area in 3 of 5. Every run was flagged with the disagreement
  quantified, so no miss goes out silently. The limit is box accuracy from a general vision model. With two
  more weeks: fit the floor and ceiling lines and the door edges with OpenCV (Hough lines) inside the
  model's rough box, or use WebXR depth on Android.

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

### E. Robustness bugs found by reading the code (no test run had hit them yet)
- **The agent trimmed its history to save tokens.** Claude Haiku 5.5 rejects a history whose earlier turns
  were changed, so a long real sweep would have failed with a 400 once it passed 40 messages. History is
  now append-only, and prompt caching keeps it cheap: 84k of 190k input tokens came from cache, and cost
  per sweep fell from $0.027 to $0.019.
- **One API error ended the sweep.** An overloaded response or network blip crashed the agent loop. Turns
  now fail softly and the conversation continues.
- **A reply cut off mid tool call left the tool unanswered,** which makes the next request invalid. Every
  tool call now gets a result.
- **Hanging up during the build cancelled it.** The packet build now runs in a shielded task, so it
  finishes and is written to disk. A failed build can be retried instead of leaving the sweep stuck.
- **One failed shelf or wall merge sank the whole packet.** Each segment now fails on its own, and the
  failure is listed first in the review queue.
- **Short titles matched the wrong listings.** "Emma" matched "Gemma's Kitchen". Short titles now need a
  whole-word match or the author's surname in the listing.
- **Shelf run counted every book in a flat stack.** A stack now counts once, at its longest book.
- **Smaller fixes:** wall label "one" crashed the room maths later (now validated), one malformed phone
  message ended the session, a refused microphone made recognition restart forever, the last shelf skipped
  the unreadable-spines check, and a room flagged as non-rectangular stayed out of the review queue.

### Smoke-test cost and latency (Claude mode, measured)
- Total model cost per 3-minute sweep: **$0.019-0.026** (Claude Haiku 5.5: agent turns + frame scans + merges; prompt caching on)
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
