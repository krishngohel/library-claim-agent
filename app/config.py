"""Settings and fixed physical constants. Everything tunable lives here."""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

SWEEPS_DIR = ROOT / "sweeps"          # every sweep writes its frames and packet here
CACHE_DIR = ROOT / ".cache"           # HTTP responses from catalog / price APIs

# Optional shared secret for the WebSocket when the app is exposed through a tunnel
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN", "")

# Models
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
LIVE_MODEL = os.getenv("LIVE_MODEL", "gemini-3.8-live")        # talks to the user
VISION_MODEL = os.getenv("VISION_MODEL", "gemini-3.8-flash")   # reads spines, finds objects
VOICE = os.getenv("VOICE", "Kore")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
# Without a Google key, everything runs on Claude + the phone's built-in speech (see app/agents.py).
_default = "gemini" if GOOGLE_API_KEY else "claude"
AGENT_PROVIDER = os.getenv("AGENT_PROVIDER", _default).lower()    # who talks: gemini (Live) or claude
VISION_PROVIDER = os.getenv("VISION_PROVIDER", _default).lower()  # who reads frames: gemini or claude
CLAUDE_AGENT_MODEL = os.getenv("CLAUDE_AGENT_MODEL", "claude-haiku-5-5")
CLAUDE_VISION_MODEL = os.getenv("CLAUDE_VISION_MODEL", "claude-haiku-5-5")

# Price sources (all optional; a missing key means "no price", never a guess)
SERPAPI_KEY = os.getenv("SERPAPI_KEY", "")
EBAY_CLIENT_ID = os.getenv("EBAY_CLIENT_ID", "")
EBAY_CLIENT_SECRET = os.getenv("EBAY_CLIENT_SECRET", "")

# Locale. Changing these two lines is all it takes to price for another country.
COUNTRY = os.getenv("COUNTRY", "US")
CURRENCY = os.getenv("CURRENCY", "USD")
LOCALES = {
    # country: (currency, google "gl" code, eBay marketplace, standard interior door height cm)
    "US": ("USD", "us", "EBAY_US", 203.2),   # 80 in
    "GB": ("GBP", "uk", "EBAY_GB", 198.1),   # 78 in (UK standard 1981 mm)
    "CA": ("CAD", "ca", "EBAY_CA", 203.2),
    "AU": ("AUD", "au", "EBAY_AU", 204.0),
}

# Anything priced above this (in local currency) goes to a human appraiser instead.
APPRAISAL_THRESHOLD = float(os.getenv("APPRAISAL_THRESHOLD", "150"))
# Books first published before this year are treated as possibly antiquarian.
ANTIQUARIAN_BEFORE_YEAR = 1950

# Known sizes of everyday objects, in centimetres (height, width).
# The vision model finds them; code turns their pixel size into a cm-per-pixel scale.
REFERENCE_OBJECTS_CM = {
    "us_letter_paper": (27.94, 21.59),
    "a4_paper": (29.7, 21.0),
    "credit_card": (5.398, 8.56),
    "us_outlet_cover": (11.43, 7.0),   # standard single-gang wall plate
    "us_light_switch_cover": (11.43, 7.0),
}

# Last-resort scale for a shelf with no reference object in any frame: assume its median spine is
# a standard trade/hardcover book height. Lines scaled this way go to the review queue.
STANDARD_BOOK_HEIGHT_CM = float(os.getenv("STANDARD_BOOK_HEIGHT_CM", "23.0"))

# Frame quality thresholds (see app/quality.py)
BLUR_THRESHOLD = 60.0     # variance of Laplacian below this = blurry
GLARE_THRESHOLD = 0.05    # more than 5% clipped-white pixels = glare

# Unit prices for the cost-per-sweep figure. Check the providers' pricing pages and update.
PRICE_VISION_IN = float(os.getenv("PRICE_VISION_IN", "0.30"))     # USD per 1M input tokens
PRICE_VISION_OUT = float(os.getenv("PRICE_VISION_OUT", "2.50"))   # USD per 1M output tokens
PRICE_LIVE_IN = float(os.getenv("PRICE_LIVE_IN", "3.00"))         # USD per 1M input tokens (audio+video)
PRICE_LIVE_OUT = float(os.getenv("PRICE_LIVE_OUT", "12.00"))      # USD per 1M output tokens (audio)
PRICE_CLAUDE_IN = float(os.getenv("PRICE_CLAUDE_IN", "0.10"))     # Claude Haiku 5.5, USD per 1M input tokens
PRICE_CLAUDE_OUT = float(os.getenv("PRICE_CLAUDE_OUT", "0.50"))   # USD per 1M output tokens
PRICE_SERPAPI_CALL = float(os.getenv("PRICE_SERPAPI_CALL", "0.015"))

SCAN_EVERY_SECONDS = 2.5  # how often a sharp frame is sent to the vision model during the sweep
