# Library Contents Claim Agent

A live voice and camera agent that inventories a home library in one walk around the room. You talk
to it while filming on your phone. It tells you when to slow down or step closer, and fills in the
inventory as you go. When you finish, it produces a claim packet: every book read from its spine,
measured and priced; every other item priced; and the room's floor and wall area.

## Run it (about 10 minutes)

Requires Python 3.11+ and either a Google AI Studio key (free, no card) or an Anthropic key.

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                 # then fill in GOOGLE_API_KEY and/or ANTHROPIC_API_KEY, and price keys
python -m uvicorn app.server:app --host 0.0.0.0 --port 8000
```

**On a laptop:** open http://localhost:8000 and click **Start sweep**.

**On a phone:** browsers only allow the camera and microphone over HTTPS, so use a tunnel:

```bash
cloudflared tunnel --url http://localhost:8000      # prints an https://....trycloudflare.com URL
```

Open that URL on the phone. If you set `ACCESS_TOKEN` in `.env`, add `?token=...` to the URL. This works
on iPhone (Safari) and Android (Chrome) with no app install.

**Keys and modes.** The app picks a mode from the keys in `.env`:

| Keys present | Voice | Frame reading | Cost per 3-min sweep |
| --- | --- | --- | --- |
| `GOOGLE_API_KEY` | Gemini Live: real-time audio both ways; the agent also watches the video | Gemini Flash | free tier |
| `ANTHROPIC_API_KEY` only | The phone's built-in speech recognition and voice (Chrome on Android/desktop, Safari on iPhone); Claude Haiku 5.5 decides what to say and calls the tools | Claude Haiku 5.5 | about $0.03 (measured) |

Force a mode with `AGENT_PROVIDER` and `VISION_PROVIDER` (`gemini` or `claude`). In the Claude mode the agent
does not watch the video itself; it hears what the camera shows from the vision scans, every 2.5 s.

**Prices.** New and used prices come from eBay's Browse API, which is free: create a developer account at
developer.eBay.com, then a Production keyset, and put the two values in `EBAY_CLIENT_ID` / `EBAY_CLIENT_SECRET`.
`SERPAPI_KEY` (Google Shopping) is optional and adds retail listings. With no price keys, every price comes
back as "no listing found": those lines are excluded from the totals and sent to the review queue. The
system never fills in a price itself.

**Tests** (no keys needed; models and price APIs are stubbed):

```bash
python -m pytest -q
```

**Smoke test** (real model calls, about $0.03 in Claude mode). Start the server, then drive it like a phone
with synthetic camera frames (4 walls, 2 bookcases) and typed speech:

```bash
python -m scripts.smoke_test
python -m eval.evaluate sweeps/<id>/claim_packet.json eval/smoke_ground_truth
python -m tests.contract sweeps/<id>/claim_packet.json      # checks the brief's output contract field by field
```

## Doing a sweep

1. Start at the door. The agent greets you and confirms the country and currency.
2. For each wall (1 to 4, clockwise from the door), step back so the whole wall is in view, floor to
   ceiling. The agent files these frames as `wall_N`.
3. Walk along each bookcase about an arm's length away, top row to bottom. The agent files these as
   `shelf_A`, `shelf_B`, and so on, and logs each shelf as you leave it.
4. Optional, but it improves scale: put a sheet of US Letter or A4 paper, or a credit card, on a shelf.
5. Say "I'm done". The agent checks for gaps (a wall not filmed, a shelf with many unreadable spines),
   asks you to re-film those, then builds the packet and reads back a summary.

Output goes to `sweeps/<id>/`:

- `claim_packet.json`: the output contract from the brief, plus extra fields
- `report.html`: built only from the JSON
- `frames/`: every saved frame, so every `frame_ref` can be opened
- `stages.jsonl`: each stage's output
- `sweep_state.json`: enough to re-run the pipeline offline

## Other commands

```bash
python -m scripts.replay <sweep_id>                 # re-run the post-sweep pipeline on saved frames
python -m scripts.second_country <sweep_id> GB      # the same 10 books priced in the UK
python -m eval.evaluate sweeps/<id>/claim_packet.json eval/ground_truth   # score against ground truth
```

Copy `eval/ground_truth_template/` to `eval/ground_truth/` and fill it in from your own room before tuning.

## Code map

| File | What it does |
| --- | --- |
| `app/server.py` | WebSocket bridge between the phone and Gemini Live, tool calls, and a live scan every 2.5 s |
| `app/live.py` | The voice agent's prompt and its 4 tools: `set_segment`, `record_statement`, `set_locale`, `end_sweep` |
| `app/agents.py` | The two interchangeable agents: Gemini Live, or Claude with the phone's own speech |
| `app/quality.py` | Blur and glare check on every frame, using OpenCV |
| `app/vision.py` | The only vision-model calls (Gemini or Claude): scan a frame, merge a shelf's frames, merge the room's items |
| `app/scale.py` | Pixels to centimetres, and room geometry. Pure math |
| `app/catalog.py` | Spine text to an Open Library record |
| `app/prices.py` | Google Shopping (SerpAPI), eBay used listings, ECB exchange rates |
| `app/valuation.py` | Pricing rules: median real listing, labelled conversion, appraisal flags |
| `app/packet.py` | Totals and review queue, computed in code |
| `app/pipeline.py` | Runs the post-sweep stages in order, timing each one |
| `app/report.py` | The HTML report |
| `web/` | Phone page: mic, camera, voice playback, live inventory |

See `ARCHITECTURE.md` for the pipeline diagram, scale and price sources.

## The reference app: what I kept, changed and threw away

I ran the reference app ([Insurance Claim Live Agent Team](https://github.com/Shubhamsaboo/awesome-llm-apps/tree/main/voice_ai_agents/insurance_claim_live_agent_team))
and read all of it before writing this.

**Kept**
- The transport: browser PCM16 mic audio at 16 kHz, JPEG camera frames, and 24 kHz PCM playback, all
  over one WebSocket to a FastAPI server that holds the Gemini Live session.
- `NON_BLOCKING` tool calls, so the agent keeps talking while background work runs, with
  `WHEN_IDLE` / `INTERRUPT` scheduling on the results.
- Barge-in: when the user talks over the agent, its queued audio is dropped.

**Changed**
- The reference agent *describes* what it sees. Here, the voice model never counts, measures or
  prices anything. A separate vision call returns boxes and text, and code turns those into numbers.
- Tools: the reference's policy lookup, claim sync, photo pin and sketch tools became `set_segment`,
  which files frames by wall or shelf, `record_statement`, `set_locale` and `end_sweep`.
- The server talks back to the agent. Frame-quality checks and scan results reach it as
  `[APP NOTICE]` messages, so it can tell the user to slow down or step closer while they are still
  standing at that shelf.
- Every frame is saved to disk with a reference, instead of only the pinned ones.

**Threw away**
- The ADK claim graph, mock policy directory, intake rules, sketch generation and avatar. None of
  them help count, measure or price.
- The localhost-only WebSocket restriction, which made phone use impossible. It is replaced by an
  optional `ACCESS_TOKEN`.

## Assumptions (where the brief is ambiguous)

- **"Replacement cost"** is the median *new* listing for the title in the local market. **"Used
  value"** is the median *used* listing, assuming the book is in "Good" condition. Using the median
  listing means each number is one real listing with a URL.
- **Edition:** an ISBN is assigned only when the publisher read on the spine matches exactly one
  edition. Otherwise the edition field names the publisher and says the exact edition can't be
  determined. Pricing then uses title and author.
- **Appraisal:** a book goes to appraisal if the binding looks antiquarian or signed, if the user says
  it is a first edition, signed or rare, or if any listing is above `APPRAISAL_THRESHOLD`
  (default 150). Art and portraits always go to appraisal unless the user says it is a print.
- **Room shape:** walls 1 and 3 face each other, as do 2 and 4. If opposite walls differ by more
  than 15%, the room is flagged as possibly non-rectangular and the mean is used. Wall area is gross
  (doors and windows not subtracted). "Shelved wall area" is the summed front area of detected shelving.
- **Item depth** can't be seen from the front, so it is reported as `null`, meaning unknown.
- **Room shape:** the sweep expects four walls, numbered clockwise from the door. An L-shaped or angled room
  shows up as opposite walls that disagree. The packet then reports the mean, lowers the confidence and puts
  the room in the review queue rather than guessing a shape.

## Tools used

- Gemini Live (`gemini-3.8-live`) for voice, or the browser's Web Speech API (speech recognition and speech
  synthesis) with Claude Haiku 5.5 deciding what to say. Gemini Flash (`gemini-3.8-flash`) or Claude Haiku 5.5
  (`claude-haiku-5-5`, `VISION_PROVIDER=claude`) for spine reading and object detection.
- Open Library (catalogue), SerpAPI Google Shopping, eBay Browse API, frankfurter.dev (ECB rates).
- OpenCV (blur and glare), RapidFuzz (fuzzy title matching), FastAPI.
- Claude Code helped write the code.
