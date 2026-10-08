"""In-memory state for one sweep, plus saving frames to disk.

Every camera frame we keep is written to sweeps/<id>/frames/ so that any
frame_ref in the packet can be opened later by an adjuster.
"""

import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app import config


@dataclass
class Frame:
    ref: str            # e.g. "f0042.jpg@31.5s" - file name plus seconds into the sweep
    path: Path
    t: float            # seconds since sweep start
    segment: str        # what the user was filming: "wall_2", "shelf_A", ... (set by the live agent)
    sharpness: float
    glare: float


@dataclass
class Sweep:
    id: str = field(default_factory=lambda: datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4])
    country: str = config.COUNTRY
    currency: str = config.CURRENCY
    device: str = ""
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None
    current_segment: str = "start"   # "wall_1".."wall_4" or a shelf label like "shelf_A"
    frames: list[Frame] = field(default_factory=list)
    scans: dict[str, dict] = field(default_factory=dict)        # frame ref -> vision scan result
    statements: list[dict] = field(default_factory=list)        # things the user told us by voice
    skipped_segments: list[str] = field(default_factory=list)  # user said "not mine"
    shelf_books: dict[str, list[dict]] = field(default_factory=dict)  # shelf -> consolidated spines
    transcript: list[dict] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=lambda: {"vision_in": 0, "vision_out": 0, "claude_in": 0, "claude_cache_write": 0, "claude_cache_read": 0, "claude_out": 0, "live_in": 0, "live_out": 0, "serpapi": 0, "ebay": 0})
    stage_seconds: dict[str, float] = field(default_factory=dict)
    warnings: list[dict] = field(default_factory=list)   # pipeline steps that failed, shown in the review queue

    @property
    def dir(self) -> Path:
        d = config.SWEEPS_DIR / self.id
        (d / "frames").mkdir(parents=True, exist_ok=True)
        return d

    def elapsed(self) -> float:
        return round((self.ended_at or time.time()) - self.started_at, 1)

    def save_frame(self, jpeg: bytes, sharpness: float, glare: float) -> Frame:
        n = len(self.frames)
        t = self.elapsed()
        path = self.dir / "frames" / f"f{n:04d}.jpg"
        path.write_bytes(jpeg)
        frame = Frame(f"{path.name}@{t}s", path, t, self.current_segment, sharpness, glare)
        self.frames.append(frame)
        return frame

    def frame(self, ref: str) -> Frame | None:
        return next((f for f in self.frames if f.ref == ref), None)

    def save_state(self) -> None:
        """Write everything needed to re-run the post-sweep pipeline offline (scripts/replay.py)."""
        state = {k: v for k, v in self.__dict__.items() if k != "frames"}
        state["frames"] = [{**f.__dict__, "path": f.path.name} for f in self.frames]
        (self.dir / "sweep_state.json").write_text(json.dumps(state, indent=1), encoding="utf-8")

    @classmethod
    def load_state(cls, sweep_id: str) -> "Sweep":
        state = json.loads((config.SWEEPS_DIR / sweep_id / "sweep_state.json").read_text(encoding="utf-8"))
        frames = state.pop("frames")
        sweep = cls(**state)
        sweep.frames = [Frame(**{**f, "path": sweep.dir / "frames" / f["path"]}) for f in frames]
        return sweep

    def log(self, name: str, payload: dict) -> None:
        """Append a stage output to sweeps/<id>/stages.jsonl so each stage can be inspected alone."""
        with open(self.dir / "stages.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"stage": name, "at": datetime.now(timezone.utc).isoformat(), **payload}) + "\n")
