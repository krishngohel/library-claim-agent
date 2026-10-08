"""End-to-end smoke test against a running server, acting like a phone.

    python -m uvicorn app.server:app --port 8000      # in one terminal
    python -m scripts.smoke_test                      # in another

Sends synthetic camera frames (a wall with a door, two bookshelves with printed titles) and typed
"speech", and prints everything the agent says and does, until the claim packet is built.
Uses real model calls (a few cents with Claude Haiku). It checks the plumbing, not accuracy:
accuracy needs a real room and eval/evaluate.py.

The true contents of the synthetic room are in eval/smoke_ground_truth/, so the packet can be scored:
    python -m eval.evaluate sweeps/<id>/claim_packet.json eval/smoke_ground_truth
"""

import asyncio
import base64
import json
import sys
import time

import cv2
import numpy as np
import websockets

URL = sys.argv[1] if len(sys.argv) > 1 else "ws://127.0.0.1:8000/ws"
rng = np.random.default_rng(0)


def texture(h, w, color):
    """A flat colour with fine noise, so the blur check sees a real-looking (sharp) photo."""
    img = np.full((h, w, 3), color, np.int16) + rng.integers(-25, 25, (h, w, 3))
    return np.clip(img, 0, 255).astype(np.uint8)


def wall_frame():
    img = texture(720, 1280, (200, 210, 215))                       # the wall, seen straight on
    img[:, :90] = texture(720, 90, (150, 160, 165))                 # left side wall (corner at x=90)
    img[:, 1190:] = texture(720, 90, (150, 160, 165))               # right side wall (corner at x=1190)
    img[640:] = texture(80, 1280, (60, 90, 120))                    # floor (floor line at y=640)
    img[:40] = texture(40, 1280, (240, 240, 240))                   # ceiling (ceiling line at y=40)
    cv2.line(img, (90, 40), (90, 640), (90, 90, 90), 3)
    cv2.line(img, (1190, 40), (1190, 640), (90, 90, 90), 3)
    cv2.rectangle(img, (180, 200), (380, 360), (30, 60, 90), 12)    # framed picture
    img[212:348, 192:368] = texture(136, 176, (90, 140, 170))
    cv2.rectangle(img, (500, 160), (700, 640), (40, 70, 110), -1)   # door, 480 px tall
    cv2.rectangle(img, (500, 160), (700, 640), (20, 30, 50), 4)
    cv2.circle(img, (680, 420), 9, (0, 200, 230), -1)               # door knob
    cv2.rectangle(img, (900, 470), (917, 497), (250, 250, 250), -1)  # outlet cover: 27 px = 11.43 cm at the door's scale
    return img


def shelf_frame(titles, offset):
    """Close view of a bookcase at 20 px/cm: spines 22 x 4 cm, a credit card (8.56 x 5.4 cm) on the shelf."""
    img = texture(720, 1280, (180, 160, 130))                       # back of bookcase
    img[:, :40] = texture(720, 40, (110, 75, 40))                   # bookcase side panels
    img[:, 1240:] = texture(720, 40, (110, 75, 40))
    img[140:170] = texture(30, 1280, (110, 75, 40))                 # shelf above
    colors = [(40, 60, 140), (30, 110, 60), (150, 140, 40), (90, 40, 110), (30, 30, 30), (20, 90, 160)]
    x = 60 - offset
    for i, title in enumerate(titles):
        spine = texture(80, 440, colors[i % len(colors)])
        if title:
            cv2.putText(spine, title, (15, 55), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (255, 255, 255), 3)
        spine = cv2.rotate(spine, cv2.ROTATE_90_CLOCKWISE)
        if 0 <= x and x + 80 <= 1280:
            img[180:620, x:x + 80] = spine
        x += 92
    img[620:650] = texture(30, 1280, (90, 60, 30))                  # shelf board
    cv2.rectangle(img, (1000, 512), (1171, 620), (200, 120, 30), -1)  # credit card leaning on the books
    cv2.rectangle(img, (1015, 535), (1045, 560), (60, 200, 230), -1)  # chip
    cv2.putText(img, "1234 5678 9012", (1010, 595), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
    return img


def jpeg(img) -> str:
    return base64.b64encode(cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes()).decode()


async def main():
    shelf_a = ["THE HOBBIT", "DUNE", "SAPIENS", "EMMA", "", "BELOVED", "ULYSSES", "WALDEN"]
    shelf_b = ["MIDDLEMARCH", "DRACULA", "", "REBECCA", "IVANHOE", "BABBITT"]
    start = time.time()
    async with websockets.connect(URL, max_size=None) as ws:

        async def listen():
            async for raw in ws:
                m = json.loads(raw)
                t = f"{time.time() - start:6.1f}s"
                if m["type"] == "say":
                    print(f"{t} AGENT SAYS: {m['text']}")
                elif m["type"] == "tool":
                    print(f"{t} TOOL {m['name']}({m['args']}) -> {json.dumps(m['result'])[:300]}")
                elif m["type"] == "notice":
                    print(f"{t} NOTICE: {m['text']}")
                elif m["type"] == "inventory":
                    print(f"{t} INVENTORY: " + ", ".join(f"{k}={v['count']}{'(final)' if v['final'] else ''}"
                                                         for k, v in m["shelves"].items()))
                elif m["type"] in ("sweep", "packet", "processing", "error"):
                    print(f"{t} {m['type'].upper()}: {json.dumps(m)[:400]}")
                    if m["type"] in ("packet", "error"):
                        return m

        listener = asyncio.create_task(listen())

        async def user(text, wait=6):
            print(f"{time.time() - start:6.1f}s USER: {text}")
            await ws.send(json.dumps({"type": "text", "text": text}))
            await asyncio.sleep(wait)

        async def film(make, seconds):
            for i in range(int(seconds * 2)):
                await ws.send(json.dumps({"type": "frame", "data": jpeg(make(i))}))
                await asyncio.sleep(0.5)

        await ws.send(json.dumps({"type": "hello", "device": "smoke-test"}))
        await asyncio.sleep(6)
        await user("Hi, yes, I'm in the US, dollars is fine.")
        for n in "1234":
            await user(f"Okay, I'm stepping back to show wall {n}.", wait=3)
            await film(lambda i: wall_frame(), 4)
        await user("The picture on wall 1 is just a print, by the way.", wait=4)
        await user("Now I'm at the first bookcase, shelf A, going along the top row.", wait=3)
        await film(lambda i: shelf_frame(shelf_a, i * 25), 8)
        await user("Moving on to the second bookcase, shelf B.", wait=3)
        await film(lambda i: shelf_frame(shelf_b, i * 20), 8)
        await user("By the way, that copy of Dracula is a signed first edition.", wait=8)
        await user("I'm done, that's the whole room.", wait=20)
        await user("That's everything, please just build the packet now.", wait=1)
        result = await asyncio.wait_for(listener, timeout=600)
        print(f"\nfinished in {time.time() - start:.0f}s: {result.get('url') if result else None}")


if __name__ == "__main__":
    asyncio.run(main())
