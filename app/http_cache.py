"""GET JSON with a disk cache.

The cache stores the time the response was actually fetched, and that is the
`retrieved_at` we report. A cached price keeps its original retrieval date,
so the packet never claims a price is fresher than it is.
"""

import hashlib
import json
from datetime import datetime, timezone

import httpx

from app import config


async def get_json(url: str, params: dict | None = None, headers: dict | None = None,
                   cache_key_extra: str = "") -> tuple[dict | None, str]:
    """Return (json or None, retrieved_at ISO timestamp)."""
    params = params or {}
    public_params = {k: v for k, v in params.items() if k != "api_key"}   # never write keys to disk
    key = hashlib.sha1(json.dumps([url, public_params, cache_key_extra], sort_keys=True).encode()).hexdigest()
    path = config.CACHE_DIR / f"{key}.json"
    if path.exists():
        cached = json.loads(path.read_text(encoding="utf-8"))
        return cached["data"], cached["retrieved_at"]

    try:
        async with httpx.AsyncClient(timeout=20, headers={"User-Agent": "library-claim-agent/0.1"}) as http:
            response = await http.get(url, params=params, headers=headers)
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError):
        return None, ""   # failed lookups are not cached, so a retry can succeed
    retrieved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    config.CACHE_DIR.mkdir(exist_ok=True)
    path.write_text(json.dumps({"url": url, "params": public_params, "retrieved_at": retrieved_at, "data": data}),
                    encoding="utf-8")
    return data, retrieved_at
