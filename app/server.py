"""Web server: serves the phone page and connects the phone to the voice agent over one WebSocket.

Per connection, three loops run side by side:
  from_phone  mic audio / speech text + camera frames in; every frame is quality-checked and saved
  agent.run   the voice agent (Gemini Live or Claude, see app/agents.py) talks back and calls tools
  scan_loop   every few seconds, the sharpest new frame goes to the vision model and the
              live inventory on the phone is updated
"""

import asyncio
import base64
import json
import logging
import time

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from rapidfuzz import fuzz

from app import config, pipeline, quality, vision
from app.agents import make_agent
from app.sweep import Sweep

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("server")
app = FastAPI()
config.SWEEPS_DIR.mkdir(exist_ok=True)
app.mount("/sweeps", StaticFiles(directory=config.SWEEPS_DIR), name="sweeps")
app.mount("/static", StaticFiles(directory=config.ROOT / "web"), name="static")


@app.get("/")
def index():
    return FileResponse(config.ROOT / "web" / "index.html")


class Connection:
    """Everything for one live sweep: the phone socket, the voice agent, and the Sweep record."""

    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.sweep = Sweep()
        self.agent = None
        self.bad_frames = 0            # consecutive blurry/glare frames
        self.last_notice = -100.0      # sweep time of the last [APP NOTICE]
        self.last_scanned = -1         # index of the last frame sent to the vision model
        self.background: set[asyncio.Task] = set()
        self.finishing = False
        self.art_asked: list[dict] = []   # art pieces the agent has already asked about

    async def send(self, **msg):
        try:
            await self.ws.send_json(msg)
        except Exception:
            pass   # phone went away; the sweep is still saved on disk

    def spawn(self, coro):
        task = asyncio.create_task(coro)
        self.background.add(task)
        task.add_done_callback(self.background.discard)
        task.add_done_callback(lambda t: t.cancelled() or not t.exception()
                               or log.error("background task failed: %r", t.exception()))

    async def notice(self, text: str, min_gap: float = 6.0, force: bool = False):
        """Tell the voice agent something. Rate-limited so it is not talked over constantly."""
        now = self.sweep.elapsed()
        if not force and now - self.last_notice < min_gap:
            return
        self.last_notice = now
        await self.agent.notice(text)
        await self.send(type="notice", text=text)

    # ---------- phone -> server ----------

    async def from_phone(self):
        while True:
            msg = json.loads(await self.ws.receive_text())
            if msg["type"] == "audio":
                await self.agent.audio(base64.b64decode(msg["data"]))
            elif msg["type"] == "frame":
                await self.on_frame(base64.b64decode(msg["data"]))
            elif msg["type"] == "text":   # typed text, or speech recognised on the phone
                await self.send(type="transcript", speaker="user", text=msg["text"] + " ")
                await self.agent.user_text(msg["text"])
            elif msg["type"] == "hello":
                self.sweep.device = msg.get("device", "")[:200]

    async def on_frame(self, jpeg: bytes):
        sharpness, glare = quality.measure(jpeg)
        frame = self.sweep.save_frame(jpeg, sharpness, glare)
        await self.agent.frame(jpeg)
        issue = quality.problem(sharpness, glare)
        self.bad_frames = self.bad_frames + 1 if issue else 0
        if self.bad_frames >= 3 and frame.segment != "start":   # ~1.5 s of bad frames: say so now
            advice = "slow down" if issue == "blurry" else "tilt the phone away from the light"
            await self.notice(f"The last frames on {frame.segment} are {issue}; ask them to {advice}.")

    # ---------- tools (called by either agent) ----------

    async def execute_tool(self, name: str, args: dict) -> dict:
        if name == "set_segment":
            result = self.set_segment(args)
        elif name == "record_statement":
            result = self.record_statement(args)
        elif name == "set_locale":
            country = str(args.get("country", "")).upper()
            if country in config.LOCALES:
                self.sweep.country, self.sweep.currency = country, config.LOCALES[country][0]
                result = {"country": country, "currency": self.sweep.currency}
            else:
                result = {"error": f"unsupported country; supported: {list(config.LOCALES)}"}
        elif name == "end_sweep":
            result = await self.end_sweep(bool(args.get("force")))
        else:
            result = {"error": "unknown tool"}
        await self.send(type="tool", name=name, args=args, result=result)
        return result

    def set_segment(self, args) -> dict:
        previous = self.sweep.current_segment
        label = str(args.get("label", "")).strip().upper()
        new = f"wall_{label}" if args.get("kind") == "wall" else f"shelf_{label}"
        self.sweep.current_segment = new
        if new.startswith("shelf_"):
            self.sweep.shelf_books.pop(new, None)   # re-filming a shelf: its old merge is out of date
        if previous.startswith("shelf_") and previous != new:
            self.spawn(self.close_shelf(previous))   # merge the finished shelf while they keep walking
        self.spawn(self.send(type="segment", segment=new))
        return {"now_filming": new}

    def record_statement(self, args) -> dict:
        statement = {"kind": args.get("kind", "other"), "about": args.get("about", ""),
                     "quote": args.get("quote", ""), "segment": self.sweep.current_segment,
                     "t": self.sweep.elapsed()}
        self.sweep.statements.append(statement)
        if statement["kind"] == "skip_current_segment":
            self.sweep.skipped_segments.append(self.sweep.current_segment)
        self.spawn(self.send(type="statement", statement=statement))
        return {"recorded": True}

    async def close_shelf(self, shelf: str):
        frames = pipeline.spread(pipeline.scanned_frames(self.sweep, shelf), config.SHELF_MERGE_MAX_FRAMES)
        if not frames or shelf in self.sweep.skipped_segments:
            return
        books = await vision.consolidate_shelf(self.sweep, shelf, frames)
        self.sweep.shelf_books[shelf] = books
        unreadable = sum(not b["legible"] for b in books)
        await self.send_inventory()
        if books and unreadable / len(books) > 0.3:
            await self.notice(f"{shelf}: {len(books)} books, {unreadable} unreadable. Ask them to go back and "
                              "re-film that shelf closer before moving on.", force=True)
        else:
            await self.notice(f"{shelf} logged: {len(books)} books, {unreadable} unreadable.", force=True)

    async def end_sweep(self, force: bool) -> dict:
        gaps = []
        for n in "1234":
            frames = pipeline.scanned_frames(self.sweep, f"wall_{n}")
            if not any(self.sweep.scans[f.ref]["wall_box"] for f in frames):
                gaps.append(f"wall {n}: never captured as a whole wall (needed for room size)")
        filmed = {f.segment for f in self.sweep.frames if f.segment.startswith("shelf_")}
        for shelf in filmed:
            if shelf not in self.sweep.skipped_segments and not pipeline.scanned_frames(self.sweep, shelf):
                gaps.append(f"{shelf}: no usable frames (all blurry or glare)")
        seen = self.bookcases_in_wall_views()
        if seen > len(filmed):
            gaps.append(f"the wall views show {seen} bookcases but only {len(filmed)} were filmed close up")
        for shelf, books in self.sweep.shelf_books.items():
            unreadable = sum(not b["legible"] for b in books)
            if books and unreadable / len(books) > 0.3:
                gaps.append(f"{shelf}: {unreadable} of {len(books)} spines unreadable")
        if gaps and not force:
            return {"ready": False, "gaps": gaps}
        if self.finishing:
            return {"error": "already building the packet"}
        self.finishing = True
        self.sweep.ended_at = time.time()
        await self.send(type="processing")
        await asyncio.gather(*[t for t in self.background if not t.done() and t is not asyncio.current_task()],
                             return_exceptions=True)   # let in-flight scans and shelf merges land first
        packet = await pipeline.finish_sweep(self.sweep)
        t = packet["totals"]
        await self.send(type="packet", url=f"/sweeps/{self.sweep.id}/report.html",
                        json_url=f"/sweeps/{self.sweep.id}/claim_packet.json", totals=t)
        return {"ready": True, "currency": self.sweep.currency, "book_count": t["book_count"],
                "books_identified": t["books_identified"], "books_unidentified": t["books_unidentified"],
                "books_replacement_cost": t["books_replacement_cost"], "books_used_value": t["books_used_value"],
                "items_count": len(packet["items"]),
                "items_replacement_cost_range": [t["items_replacement_cost_low"], t["items_replacement_cost_high"]],
                "floor_area_m2": packet["room"]["floor_area_m2"], "lines_to_review": len(packet["review_queue"]),
                "excluded_from_totals": t["excluded_from_totals"], "known_gaps": gaps}

    def bookcases_in_wall_views(self) -> int:
        """Bookcases visible in the whole-wall views: for each wall, the most seen in any one frame."""
        total = 0
        for n in "1234":
            frames = pipeline.scanned_frames(self.sweep, f"wall_{n}")
            total += max((sum(i["category"] == "shelving" for i in self.sweep.scans[f.ref]["items"])
                          for f in frames), default=0)
        return total

    # ---------- live scanning ----------

    async def scan_loop(self):
        while not self.finishing:
            await asyncio.sleep(config.SCAN_EVERY_SECONDS)
            new = [f for i, f in enumerate(self.sweep.frames) if i > self.last_scanned
                   and quality.problem(f.sharpness, f.glare) is None and f.segment != "start"]
            if not new:
                continue
            self.last_scanned = len(self.sweep.frames) - 1
            self.spawn(self.scan(max(new, key=lambda f: f.sharpness)))   # sharpest usable frame since last scan

    async def scan(self, frame):
        try:
            self.sweep.scans[frame.ref] = await vision.scan_frame(self.sweep, frame)
        except Exception as exc:   # one failed scan should never end the sweep
            log.warning("scan failed for %s: %r", frame.ref, exc)
            return
        scan = self.sweep.scans[frame.ref]
        await self.send_inventory()
        await self.ask_about_art(scan, frame.segment)
        if frame.segment.startswith("wall_"):
            await self.coach_wall(frame, scan)
        else:
            await self.coach_shelf(frame, scan)

    async def coach_wall(self, frame, scan):
        """Wall views are for measuring the room: we need the whole wall in frame."""
        if self.wall_done(frame.segment):
            return
        if scan["wall_box"] is None:
            await self.notice(f"{frame.segment}: the whole wall is not in view (need both corners, the floor line and "
                              "the ceiling line). Ask them to step back or turn until it all fits.")
        else:
            await self.notice(f"{frame.segment} captured for room measurement. They can move on.", force=True)

    def wall_done(self, segment: str) -> bool:
        """True once a frame of this wall has already captured the whole wall."""
        return sum(1 for f in pipeline.scanned_frames(self.sweep, segment) if self.sweep.scans[f.ref]["wall_box"]) > 1

    async def coach_shelf(self, frame, scan):
        unreadable = sum(not s["legible"] for s in scan["spines"])
        if not scan["spines"]:
            await self.notice(f"{frame.segment}: no book spines in view. Ask them to point the camera at the shelf, "
                              "about an arm's length away.")
        elif unreadable / len(scan["spines"]) > 0.4 or scan["capture_issues"]:
            await self.notice(f"{frame.segment}: {len(scan['spines'])} spines in view, {unreadable} unreadable. "
                              f"Issues: {'; '.join(scan['capture_issues']) or 'text too small'}. Ask them to step "
                              "closer or slow down on this part of the shelf.")
        else:
            await self.notice(f"{frame.segment}: {len(scan['spines'])} spines in view, all readable.", min_gap=15)

    async def ask_about_art(self, scan, segment: str):
        """Art defaults to appraisal unless the user says it is a print, so the agent asks once per piece.

        Each piece gets an id (ART1, ART2 ...). Later sightings of a similar piece on the same wall or shelf
        get the same id, and the agent passes the id back in record_statement, so the answer reaches the
        right item in the packet.
        """
        for item in scan["items"]:
            if item["category"] not in ("art", "portrait"):
                continue
            same = [a for a in self.art_asked if a["segment"] == segment
                    and fuzz.token_set_ratio(item["description"].lower(), a["description"]) >= 50]
            if same:
                item["art_id"] = same[0]["id"]
                continue
            item["art_id"] = f"ART{len(self.art_asked) + 1}"
            self.art_asked.append({"id": item["art_id"], "segment": segment, "description": item["description"].lower()})
            await self.notice(f"Art seen ({item['art_id']}): {item['description']}. Ask whether it is an original or "
                              f"a print, and record the answer with record_statement (is_print or is_original) "
                              f"with about='{item['art_id']}'.", force=True)

    async def send_inventory(self):
        """Live inventory for the phone. Merged shelves are exact; open shelves are a running view."""
        shelves = {}
        for shelf in {f.segment for f in self.sweep.frames if f.segment.startswith("shelf_")}:
            if shelf in self.sweep.shelf_books:
                books = self.sweep.shelf_books[shelf]
                shelves[shelf] = {"final": True, "count": len(books),
                                  "titles": [b["title"] for b in books if b["legible"]],
                                  "unreadable": sum(not b["legible"] for b in books)}
                continue
            titles, most = [], 0
            for f in pipeline.scanned_frames(self.sweep, shelf):
                spines = self.sweep.scans[f.ref]["spines"]
                most = max(most, len(spines))
                for s in spines:   # display-only de-dup of titles seen in overlapping frames
                    if s["legible"] and not any(fuzz.ratio(s["title"].lower(), t.lower()) > 85 for t in titles):
                        titles.append(s["title"])
            shelves[shelf] = {"final": False, "count": most, "titles": titles, "unreadable": None}
        items = {}
        for scan in self.sweep.scans.values():
            for it in scan["items"]:
                items.setdefault(it["category"], set()).add(it["description"])
        await self.send(type="inventory", shelves=shelves,
                        items={k: sorted(v) for k, v in items.items()},
                        skipped=self.sweep.skipped_segments)


@app.websocket("/ws")
async def live_socket(ws: WebSocket):
    if config.ACCESS_TOKEN and ws.query_params.get("token") != config.ACCESS_TOKEN:
        await ws.close(code=1008)
        return
    await ws.accept()
    missing = config.missing_keys()
    if missing:
        await ws.send_json({"type": "error", "message": missing})
        await ws.close()
        return
    conn = Connection(ws)
    conn.agent = make_agent(conn)
    await conn.send(type="sweep", id=conn.sweep.id, browser_voice=conn.agent.browser_voice,
                    agent=config.AGENT_PROVIDER, vision=config.VISION_PROVIDER)
    try:
        async with conn.agent:
            loops = [asyncio.create_task(c) for c in (conn.from_phone(), conn.agent.run(), conn.scan_loop())]
            done, _ = await asyncio.wait(loops, return_when=asyncio.FIRST_EXCEPTION)
            for task in loops:
                task.cancel()
            for task in done:
                if task.exception() and not isinstance(task.exception(), WebSocketDisconnect):
                    raise task.exception()
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.exception("live session ended")
        await conn.send(type="error", message=f"Live session ended: {exc}")
    finally:
        if not conn.finishing:   # if the packet is being built, let it finish even if the phone hangs up
            for task in list(conn.background):
                task.cancel()
