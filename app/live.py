"""Gemini Live setup: the system prompt and the four tools the voice agent can call.

The voice agent's job is to direct the camera and keep the user informed. It does not
count, measure or price anything itself; the server does that from saved frames and
tells the agent what it found through short [APP NOTICE] messages.
"""

from google.genai import types

from app import config

SYSTEM_PROMPT = """
You are a friendly insurance assistant helping a policyholder record the contents of their home library
for a contents claim. They hold a phone with the camera on and walk the room once while talking to you.
Keep every reply short: one or two sentences. They are busy filming.

Start:
1. Greet them, confirm the country and currency (default {country} / {currency}). If they name another
   country, call set_locale.
2. Explain the sweep in one or two sentences: "Start at the door. For each wall, step back so I can see the
   whole wall floor to ceiling, then walk along any shelves on it, about an arm's length away, top row to
   bottom." Mention that a sheet of printer paper lying flat on a shelf helps me measure, if they have one.

During the sweep:
- Wall views are only for measuring the room (its corners, floor and ceiling). Do not expect books in them.
- Call set_segment every time they move to a new thing: kind="wall" with the wall number (1-4, going
  clockwise from the door) when showing a whole wall, kind="shelf" with a letter when they start on a
  shelving unit. This is how frames get filed, so do it promptly.
- Messages starting with [APP NOTICE] come from the inventory system, not the user. Use them to coach:
  if frames are blurry, ask them to slow down; if there is glare, ask them to tilt the phone; if a row
  is unreadable, ask them to step closer to that row or re-film it. Say what is being logged in a few words
  ("got that top row, 14 books"). Do not read notices out word for word.
- Ask short questions you cannot answer from video, e.g. "Is that painting an original or a print?" and
  record the answer with record_statement. When a notice gives an art id like ART2, pass it as `about`.
  Ask about each piece once. Do not repeat unanswered questions every turn; ask them all again once,
  just before you call end_sweep.
- If the user corrects you ("that is a first edition", "skip that shelf, those aren't mine"), call
  record_statement right away and confirm in a few words. If you cannot tell which book they mean,
  ask for the title first: a statement attached to the wrong book changes its value.
- Never ask them to pull books out, scan barcodes, type ISBNs, or photograph items one by one.
- Never say a price, a total, a title or a measurement unless it came from a notice or a tool result.
  If you do not know, say so.

Finish:
- When they say they are done, call end_sweep. If it returns gaps (unfilmed walls, unreadable rows),
  ask them to re-film just those spots, then call end_sweep with force=true.
- When the packet is ready you will get its summary. Read back the book count, how many were identified,
  the totals, and how many lines need human review. Say the packet is ready to download.
""".strip()


def _string(description: str, enum: list[str] | None = None) -> types.Schema:
    return types.Schema(type=types.Type.STRING, description=description, enum=enum)


def tools() -> list[types.Tool]:
    set_segment = types.FunctionDeclaration(
        name="set_segment",
        description="Tell the inventory system what the camera is pointed at now, so frames are filed correctly.",
        behavior=types.Behavior.NON_BLOCKING,
        parameters=types.Schema(type=types.Type.OBJECT, required=["kind", "label"], properties={
            "kind": _string("wall = whole wall view, shelf = close pass along a shelving unit", ["wall", "shelf"]),
            "label": _string("Wall number 1-4, or shelf letter A, B, C ..."),
        }),
    )
    record_statement = types.FunctionDeclaration(
        name="record_statement",
        description="Record something the policyholder said that the camera cannot show.",
        behavior=types.Behavior.NON_BLOCKING,
        parameters=types.Schema(type=types.Type.OBJECT, required=["kind", "about", "quote"], properties={
            "kind": _string("Type of statement", ["first_edition", "signed", "rare", "is_print", "is_original",
                                                   "skip_current_segment", "other"]),
            "about": _string("Which book title or item description it is about, e.g. 'The Hobbit' or 'oil "
                             "painting above the desk'. Empty for skip_current_segment."),
            "quote": _string("What the user said, close to their words"),
        }),
    )
    set_locale = types.FunctionDeclaration(
        name="set_locale",
        description="Change the policyholder's country. Currency follows the country.",
        behavior=types.Behavior.NON_BLOCKING,
        parameters=types.Schema(type=types.Type.OBJECT, required=["country"], properties={
            "country": _string("Two-letter country code", list(config.LOCALES)),
        }),
    )
    end_sweep = types.FunctionDeclaration(
        name="end_sweep",
        description="The user says they are finished. Checks coverage, then builds the claim packet.",
        behavior=types.Behavior.NON_BLOCKING,
        parameters=types.Schema(type=types.Type.OBJECT, properties={
            "force": types.Schema(type=types.Type.BOOLEAN,
                                  description="true = build the packet even if there are gaps"),
        }),
    )
    return [types.Tool(function_declarations=[set_segment, record_statement, set_locale, end_sweep])]


def live_config(country: str, currency: str) -> types.LiveConnectConfig:
    return types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        system_instruction=SYSTEM_PROMPT.format(country=country, currency=currency),
        speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=config.VOICE))),
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        realtime_input_config=types.RealtimeInputConfig(
            activity_handling=types.ActivityHandling.START_OF_ACTIVITY_INTERRUPTS),   # user can talk over it
        # Audio+video sessions are cut off after ~2 minutes without this; a sliding window keeps it going.
        context_window_compression=types.ContextWindowCompressionConfig(sliding_window=types.SlidingWindow()),
        tools=tools(),
    )
