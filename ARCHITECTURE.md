# Architecture

```
 PHONE (browser)                         SERVER (FastAPI)                               OUTSIDE SERVICES
 ───────────────                         ────────────────                               ────────────────
 mic  16 kHz PCM ──────────┐
 camera JPEG 2/s ──────────┼──► on_frame: blur/glare (OpenCV) ──► save frames/f0001.jpg
                           │         │ 3 bad frames in a row ──► [APP NOTICE] "slow down"
                           │         ▼
                           └──────► Gemini Live (voice) ◄──── [APP NOTICE]s ─────┐
 speaker 24 kHz PCM ◄──────────────  │ tools: set_segment, record_statement,     │
 live inventory ◄─────────┐          │        set_locale, end_sweep              │
                          │          ▼                                           │
                          │   scan_loop (every 2.5 s, sharpest new frame)        │
                          ├─── vision.scan_frame ─────────────────────► Gemini Flash: spines, items,
                          │                                             references, wall box (boxes + text only)
                          └─── close_shelf when user moves on ─────────► Gemini Flash: merge overlapping frames
                                                                         into one list per shelf
 end_sweep ─► coverage check (all 4 walls? unreadable shelves?) ─► re-film or continue
          └─► pipeline.finish_sweep
                1 books_consolidate  (any shelf not yet merged)          Gemini Flash
                2 books_identify     spine text ─► catalogue record      Open Library
                3 items_consolidate  one entry per physical object       Gemini Flash
                4 measure            boxes ─► cm, walls ─► room           code only (app/scale.py)
                5 price              median real listing per line        SerpAPI, eBay, ECB rates
                6 packet             totals + review queue + report      code only (app/packet.py)
          ─► claim_packet.json, report.html, frames/, stages.jsonl
```

## Which model does what

| Job | Model | Output |
| --- | --- | --- |
| Talk to the user and direct the sweep | Gemini Live, or Claude Haiku 5.5 with the phone's own speech recognition and voice (`app/agents.py`) | Speech, tool calls. It never produces a number for the packet |
| Find and read spines, items, reference objects, wall extent | Gemini Flash (or Claude Haiku 5.5 via `VISION_PROVIDER`), same Pydantic schema | Boxes in a 0-1000 grid, plus text it can actually read. Blank if it can't |
| Decide which sightings in overlapping frames are the same book or object | Gemini Flash or Claude Haiku, once per shelf or wall | Picks by (frame number, index) from the existing list. Picks that point nowhere, and duplicates, are dropped in code |
| Merge objects across walls and shelves | Code (`pipeline.merge_across_segments`) | Same category, similar description, and a place where one object could be seen twice: a wall and its shelf, or neighbouring walls with the object near the corner. Opposite walls and different bookcases never merge |
| Everything numeric | Python | Counts, centimetres, areas, totals |

## Where metric scale comes from

Measured per frame, in this order (`app/scale.py`, `app/pipeline.py:frame_scale`):

1. **Reference object in the same frame, seen straight on and fully visible:**
   - an interior door: 203.2 cm in the US, 198.1 cm in the UK
   - US Letter or A4 paper
   - a credit card
   - a US outlet or switch cover plate (11.43 cm)

   Pixels per cm = pixel length of the object's long side ÷ its real length. If several kinds are
   visible, only the largest kind is used. A few pixels of box error is 1% of a door but 10% of an outlet
   plate, and a large object is harder to misidentify. In testing, a picture frame mistaken for paper
   pushed the floor area 52% off; using the largest reference brought it to 9%.
2. **A catalogued book in the same frame:** a book whose exact edition was identified and whose
   Open Library record has physical dimensions.
3. **The median scale of other frames of the same shelf.** The line is flagged for review.
4. **Standard book format:** if no frame of the shelf has any reference, the shelf's median spine is
   assumed to be 23 cm tall, a standard trade or hardcover height. The brief allows standard book formats.
   The line is flagged for review as low confidence.
5. **None of the above:** the dimensions are left blank and the line is sent to review.

**Room.** Wall frames (`wall_1`…`wall_4`) use the door as the scale for wall width and ceiling height.
A wall with no door or other reference uses the ceiling height measured on the other walls as its
scale. Length is the mean of walls 1 and 3, width the mean of walls 2 and 4. Then:

- floor area = length × width
- wall area = 2 × (length + width) × height

Known weakness: the door sits on the wall plane, but objects in front of the wall, such as shelf
fronts, are about 30 cm closer to the camera. At a 3 m standing distance that is roughly 10% scale
error. That's why shelf frames prefer paper on the shelf, or catalogued books, over a door.

## Where prices come from

| Figure | Source | Rule |
| --- | --- | --- |
| Book replacement cost | Google Shopping via SerpAPI, local `gl` country | Median *new* listing whose title matches the book (fuzzy match ≥ 85) |
| Book replacement cost (free path) | eBay Browse API, condition NEW, on the local marketplace | Median new listing, combined with the Shopping listings if SerpAPI is set |
| Book used value | eBay Browse API on the local marketplace (`EBAY_US`, `EBAY_GB`), condition USED, plus used Shopping listings | Median used listing |
| No local listing | Same sources for the other market, converted at the ECB rate | `converted: true`, rate and date in `source`, sent to review |
| No listing anywhere | none | Amount left blank, excluded from totals, sent to review |
| Item, brand legible | Google Shopping | Median listing matching the brand or model |
| Item, brand unknown | Google Shopping, using the description | 25th–75th percentile listings as a range |
| Art, signed, antiquarian, above threshold | none | `needs_appraisal`, never auto-priced |

Every price is one real listing. Its amount, URL and `retrieved_at` come straight from the API
response. The disk cache keeps the original retrieval time, so a cached price is never presented as fresh.
