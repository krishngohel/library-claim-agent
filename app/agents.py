"""The two interchangeable voice agents. Both call the same four tools on the Connection.

GeminiAgent  - Gemini Live: raw mic audio in, spoken audio out, sees the camera frames itself.
ClaudeAgent  - the phone's own speech recognition sends text; Claude answers in text; the phone
               speaks it with its built-in voice. Claude does not watch the video; it learns what
               the camera sees from the [APP NOTICE] scan summaries (which come from the vision model).

Pick with AGENT_PROVIDER (default: gemini if GOOGLE_API_KEY is set, else claude).
"""

import asyncio
import base64
import logging

import anthropic

from app import config, live, vision

log = logging.getLogger("agents")


class GeminiAgent:
    browser_voice = False   # the phone streams raw audio and plays raw audio

    def __init__(self, conn):
        self.conn = conn
        self.session = None
        self._cm = None

    async def __aenter__(self):
        from google.genai import types
        self.types = types
        self._cm = vision.client().aio.live.connect(
            model=config.LIVE_MODEL, config=live.live_config(self.conn.sweep.country, self.conn.sweep.currency))
        self.session = await self._cm.__aenter__()
        await self.session.send_realtime_input(text="[APP NOTICE] The user has connected. Greet them.")
        return self

    async def __aexit__(self, *exc):
        await self._cm.__aexit__(*exc)

    async def audio(self, pcm: bytes):
        await self.session.send_realtime_input(audio=self.types.Blob(data=pcm, mime_type="audio/pcm;rate=16000"))

    async def frame(self, jpeg: bytes):
        await self.session.send_realtime_input(video=self.types.Blob(data=jpeg, mime_type="image/jpeg"))

    async def user_text(self, text: str):
        await self.session.send_realtime_input(text=text)

    async def notice(self, text: str):
        await self.session.send_realtime_input(text=f"[APP NOTICE] {text}")

    async def run(self):
        """Forward Gemini's voice and transcripts to the phone; run tool calls in the background."""
        types, conn = self.types, self.conn
        while True:
            async for response in self.session.receive():
                if response.usage_metadata:
                    conn.sweep.usage["live_in"] += response.usage_metadata.prompt_token_count or 0
                    conn.sweep.usage["live_out"] += response.usage_metadata.response_token_count or 0
                if response.tool_call:
                    for call in response.tool_call.function_calls:
                        conn.spawn(self._tool(call))
                content = response.server_content
                if not content:
                    continue
                if content.interrupted:
                    await conn.send(type="interrupted")   # phone drops queued agent audio
                if content.input_transcription and content.input_transcription.text:
                    await conn.send(type="transcript", speaker="user", text=content.input_transcription.text)
                if content.output_transcription and content.output_transcription.text:
                    await conn.send(type="transcript", speaker="agent", text=content.output_transcription.text)
                for part in (content.model_turn.parts if content.model_turn else None) or []:
                    if part.inline_data and part.inline_data.mime_type.startswith("audio/pcm"):
                        await conn.send(type="audio", data=base64.b64encode(part.inline_data.data).decode())

    async def _tool(self, call):
        result = await self.conn.execute_tool(call.name, dict(call.args or {}))
        scheduling = (self.types.FunctionResponseScheduling.INTERRUPT if call.name == "end_sweep"
                      else self.types.FunctionResponseScheduling.WHEN_IDLE)
        try:
            await self.session.send_tool_response(function_responses=[
                self.types.FunctionResponse(id=call.id, name=call.name, response=result, scheduling=scheduling)])
        except Exception:
            log.warning("could not return %s result (live session closed?)", call.name)


# Same four tools as app/live.py, written as Anthropic tool definitions.
CLAUDE_TOOLS = [
    {"name": "set_segment",
     "description": "Tell the inventory system what the camera is pointed at now, so frames are filed correctly.",
     "input_schema": {"type": "object", "required": ["kind", "label"], "properties": {
         "kind": {"type": "string", "enum": ["wall", "shelf"],
                  "description": "wall = whole wall view, shelf = close pass along a shelving unit"},
         "label": {"type": "string", "description": "Wall number 1-4, or shelf letter A, B, C ..."}}}},
    {"name": "record_statement",
     "description": "Record something the policyholder said that the camera cannot show.",
     "input_schema": {"type": "object", "required": ["kind", "about", "quote"], "properties": {
         "kind": {"type": "string", "enum": ["first_edition", "signed", "rare", "is_print", "is_original",
                                             "skip_current_segment", "other"]},
         "about": {"type": "string", "description": "Book title or item description it is about. Empty for skip_current_segment."},
         "quote": {"type": "string", "description": "What the user said, close to their words"}}}},
    {"name": "set_locale",
     "description": "Change the policyholder's country. Currency follows the country.",
     "input_schema": {"type": "object", "required": ["country"], "properties": {
         "country": {"type": "string", "enum": list(config.LOCALES)}}}},
    {"name": "end_sweep",
     "description": "The user says they are finished. Checks coverage, then builds the claim packet (takes a few minutes).",
     "input_schema": {"type": "object", "properties": {
         "force": {"type": "boolean", "description": "true = build the packet even if there are gaps"}}}},
]

BROWSER_VOICE_NOTE = """
In this mode you cannot see the camera yourself. You hear the user as text from their phone's speech
recognition (it may contain small mistakes), and you learn what the camera shows only from [APP NOTICE]
messages. Your reply is read aloud by the phone, so write plain spoken sentences: no lists, no markdown,
no emoji. If an [APP NOTICE] needs no comment, reply with an empty message.
"""


class ClaudeAgent:
    browser_voice = True   # the phone does speech-to-text and text-to-speech itself

    def __init__(self, conn):
        self.conn = conn
        self.messages: list[dict] = []
        self.inbox: asyncio.Queue[str] = asyncio.Queue()   # user speech and notices, handled one at a time
        self.client = vision.claude()
        self.system = (live.SYSTEM_PROMPT.format(country=conn.sweep.country, currency=conn.sweep.currency)
                       + "\n" + BROWSER_VOICE_NOTE)

    async def __aenter__(self):
        await self.inbox.put("[APP NOTICE] The user has connected. Greet them.")
        return self

    async def __aexit__(self, *exc):
        pass

    async def audio(self, pcm: bytes):
        pass   # not used: speech arrives as text

    async def frame(self, jpeg: bytes):
        pass   # not used: Claude learns what is on camera from scan notices

    async def user_text(self, text: str):
        await self.inbox.put(f"User said: {text}")

    async def notice(self, text: str):
        await self.inbox.put(f"[APP NOTICE] {text}")

    async def run(self):
        while True:
            first = await self.inbox.get()
            batch = [first]
            while not self.inbox.empty():   # several notices queued while we were busy -> one turn
                batch.append(self.inbox.get_nowait())
            try:
                await self.turn("\n".join(batch))
            except anthropic.APIError as exc:   # the SDK already retried; keep the sweep alive regardless
                log.warning("agent turn failed: %r", exc)
                await self.conn.send(type="say", text="Sorry, I lost my connection for a moment. Keep going.")

    async def turn(self, text: str):
        """One user turn: call Claude, run any tools it asks for, repeat until it answers in words.

        The history is append-only: nothing is ever removed or edited. Current Claude models reject a
        history whose earlier turns were changed (the "history-editing check"), so instead of trimming
        old turns we let prompt caching make the repeated history cheap (cache reads cost ~10%).
        """
        self.messages.append({"role": "user", "content": text})
        while True:
            response = await self.client.messages.create(
                model=config.CLAUDE_AGENT_MODEL, max_tokens=4000, system=self.system, tools=CLAUDE_TOOLS,
                messages=self.messages, output_config={"effort": "low"}, cache_control={"type": "ephemeral"})
            usage = response.usage
            self.conn.sweep.usage["claude_in"] += usage.input_tokens
            self.conn.sweep.usage["claude_cache_write"] += usage.cache_creation_input_tokens or 0
            self.conn.sweep.usage["claude_cache_read"] += usage.cache_read_input_tokens or 0
            self.conn.sweep.usage["claude_out"] += usage.output_tokens
            self.messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "refusal":
                await self.conn.send(type="say", text="I can't help with that one. Let's carry on with the sweep.")
                return
            spoken = " ".join(b.text for b in response.content if b.type == "text").strip()
            if spoken:
                await self.conn.send(type="say", text=spoken)
                await self.conn.send(type="transcript", speaker="agent", text=spoken + " ")

            # Every tool call must get a result, even if the reply was cut off, or the next request is invalid.
            calls = [b for b in response.content if b.type == "tool_use"]
            if not calls:
                return
            results = await asyncio.gather(*(self.conn.execute_tool(c.name, dict(c.input)) for c in calls))
            self.messages.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": c.id, "content": str(r)} for c, r in zip(calls, results)]})


def make_agent(conn):
    return ClaudeAgent(conn) if config.AGENT_PROVIDER == "claude" else GeminiAgent(conn)
