// Phone client. Sends camera frames (JPEG, 2 per second) and the user's voice to the server,
// plays the agent's voice, and shows the live inventory.
//
// Two voice modes, chosen by the server (see app/agents.py):
//   Gemini Live:   raw mic audio (16 kHz PCM) up, raw agent audio (24 kHz PCM) down.
//   Browser voice: the phone's own speech recognition turns speech into text, and the phone's
//                  own speech synthesis reads the agent's replies aloud. No Google key needed.

const FRAME_INTERVAL_MS = 500;
const $ = (id) => document.getElementById(id);
let ws, audioCtx, frameTimer, playAt = 0;
let playing = [];               // Gemini audio chunks queued, so we can stop them when the user interrupts
let lastSpeaker = null, lastLine = null;
let recognizer = null, speakingText = "";

$("start").onclick = start;
$("say-form").onsubmit = (e) => {
  e.preventDefault();
  const text = $("say").value.trim();
  if (text && ws?.readyState === 1) ws.send(JSON.stringify({ type: "text", text }));
  $("say").value = "";
};

async function start() {
  $("start").disabled = true;
  audioCtx = new AudioContext();   // must be created from a tap on iOS
  speechSynthesis.speak(new SpeechSynthesisUtterance(""));   // iOS only allows speech that a tap started
  const token = new URLSearchParams(location.search).get("token") || "";
  ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws?token=${token}`);
  ws.onmessage = (e) => handle(JSON.parse(e.data));
  ws.onclose = () => { $("status").textContent = "Disconnected"; clearInterval(frameTimer); recognizer?.abort(); };
}

// The server's first message says which voice mode to use; only then do we open the camera/mic.
async function begin(browserVoice) {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: browserVoice ? false : { echoCancellation: true, noiseSuppression: true },
    video: { facingMode: "environment", width: { ideal: 1280 }, height: { ideal: 720 } },
  });
  $("camera").srcObject = stream;
  ws.send(JSON.stringify({ type: "hello", device: navigator.userAgent }));
  frameTimer = setInterval(sendFrame, FRAME_INTERVAL_MS);
  if (browserVoice) startRecognition();
  else startMic(stream);
  $("status").textContent = "Live";
}

// ---------- Gemini Live mode: raw audio ----------

function startMic(stream) {
  const source = audioCtx.createMediaStreamSource(new MediaStream(stream.getAudioTracks()));
  const node = audioCtx.createScriptProcessor(4096, 1, 1);
  node.onaudioprocess = (e) => {
    if (ws.readyState !== 1) return;
    const pcm = toPcm16(e.inputBuffer.getChannelData(0), audioCtx.sampleRate, 16000);
    ws.send(JSON.stringify({ type: "audio", data: toBase64(pcm.buffer) }));
  };
  source.connect(node);
  node.connect(audioCtx.destination);   // required for the processor to run; it outputs silence
}

// Downsample float audio to 16-bit PCM at the target rate (simple decimation).
function toPcm16(input, fromRate, toRate) {
  const ratio = fromRate / toRate, out = new Int16Array(Math.floor(input.length / ratio));
  for (let i = 0; i < out.length; i++) {
    const s = Math.max(-1, Math.min(1, input[Math.floor(i * ratio)]));
    out[i] = s * 0x7fff;
  }
  return out;
}

function toBase64(buffer) {
  let binary = "";
  const bytes = new Uint8Array(buffer);
  for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
  return btoa(binary);
}

function playPcm24(base64) {
  const bytes = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
  const pcm = new Int16Array(bytes.buffer);
  const buffer = audioCtx.createBuffer(1, pcm.length, 24000);
  const channel = buffer.getChannelData(0);
  for (let i = 0; i < pcm.length; i++) channel[i] = pcm[i] / 0x8000;
  const src = audioCtx.createBufferSource();
  src.buffer = buffer;
  src.connect(audioCtx.destination);
  playAt = Math.max(playAt, audioCtx.currentTime);   // queue chunks back to back
  src.start(playAt);
  playAt += buffer.duration;
  playing.push(src);
  src.onended = () => (playing = playing.filter((s) => s !== src));
}

// ---------- Browser voice mode: phone speech recognition + speech synthesis ----------

function startRecognition() {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Recognition) {
    addNotice("This browser has no speech recognition. Use Chrome (Android/desktop) or Safari (iPhone), or type below.");
    return;
  }
  recognizer = new Recognition();
  recognizer.continuous = true;
  recognizer.interimResults = true;
  recognizer.lang = "en-US";
  recognizer.onresult = (event) => {
    const result = event.results[event.results.length - 1];
    const text = result[0].transcript.trim();
    if (!text) return;
    if (speechSynthesis.speaking) {
      if (isEcho(text)) return;   // the mic heard the agent's own voice
      speechSynthesis.cancel();   // the user is talking over the agent: stop speaking
    }
    if (result.isFinal) ws.send(JSON.stringify({ type: "text", text }));
  };
  // Phones stop recognition after silence or errors; keep restarting it for the whole sweep.
  recognizer.onend = () => { if (ws.readyState === 1) setTimeout(() => { try { recognizer.start(); } catch {} }, 250); };
  recognizer.onerror = (e) => { if (e.error === "not-allowed") addNotice("Microphone blocked: allow it, or type below."); };
  recognizer.start();
}

// Treat recognised text as echo if most of its words are in what the agent is currently saying.
function isEcho(text) {
  const spoken = new Set(speakingText.toLowerCase().match(/[a-z0-9']+/g) || []);
  const heard = text.toLowerCase().match(/[a-z0-9']+/g) || [];
  return heard.length > 0 && heard.filter((w) => spoken.has(w)).length / heard.length >= 0.5;
}

function say(text) {
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = "en-US";
  utterance.onstart = () => (speakingText = text);
  utterance.onend = () => (speakingText = "");
  speechSynthesis.speak(utterance);   // queues after anything already being said
}

// ---------- camera ----------

const canvas = document.createElement("canvas");
function sendFrame() {
  const video = $("camera");
  if (!video.videoWidth || ws.readyState !== 1) return;
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  canvas.getContext("2d").drawImage(video, 0, 0);
  const data = canvas.toDataURL("image/jpeg", 0.8).split(",")[1];
  ws.send(JSON.stringify({ type: "frame", data }));
}

// ---------- messages from the server ----------

function handle(msg) {
  switch (msg.type) {
    case "sweep":
      $("status").textContent = `Connecting (${msg.agent} voice, ${msg.vision} vision)...`;
      begin(msg.browser_voice).catch((err) => ($("status").textContent = "Camera/mic error: " + err.message));
      return;
    case "audio": return playPcm24(msg.data);
    case "say": return say(msg.text);
    case "interrupted":   // user talked over the agent: drop what is queued
      playing.forEach((s) => s.stop());
      playing = []; playAt = 0;
      return;
    case "transcript": return addTranscript(msg.speaker, msg.text);
    case "segment": $("segment").textContent = "Filming: " + msg.segment.replace("_", " "); return;
    case "notice": return addNotice(msg.text);
    case "inventory": return renderInventory(msg);
    case "statement": return addNotice(`Noted: ${msg.statement.kind.replaceAll("_", " ")} ${msg.statement.about}`);
    case "processing": $("status").textContent = "Building claim packet..."; return;
    case "packet":
      $("status").textContent = "Packet ready";
      $("packet").hidden = false;
      $("packet").innerHTML = `<b>Claim packet ready.</b> <a href="${msg.url}" target="_blank">Report</a> |
        <a href="${msg.json_url}" target="_blank">claim_packet.json</a><br>
        ${msg.totals.book_count} books, ${msg.totals.excluded_from_totals} lines excluded from totals`;
      return;
    case "error": $("status").textContent = msg.message; return;
  }
}

function addTranscript(speaker, text) {
  if (speaker !== lastSpeaker) {   // new line whenever the speaker changes; chunks of one turn join up
    lastLine = document.createElement("p");
    lastLine.className = speaker;
    lastLine.textContent = speaker === "agent" ? "Agent: " : "You: ";
    $("transcript").prepend(lastLine);
    lastSpeaker = speaker;
  }
  lastLine.textContent += text;
}

function addNotice(text) {
  const div = document.createElement("div");
  div.textContent = text;
  $("notices").prepend(div);
  while ($("notices").children.length > 3) $("notices").lastChild.remove();
}

function renderInventory(msg) {
  let total = 0;
  $("shelves").innerHTML = "";
  for (const [shelf, s] of Object.entries(msg.shelves).sort()) {
    total += s.count;
    const div = document.createElement("div");
    div.className = "shelf";
    const skipped = msg.skipped.includes(shelf) ? " (skipped - not yours)" : "";
    const status = s.final ? `${s.count} books, ${s.unreadable} unreadable` : `scanning... up to ${s.count} spines in view`;
    div.innerHTML = `<b></b> <small></small><br>`;
    div.querySelector("b").textContent = shelf.replace("_", " ") + skipped;
    div.querySelector("small").textContent = status;
    div.append(s.titles.join(" · "));
    $("shelves").append(div);
  }
  $("count").textContent = `(${total} books so far)`;
  const items = Object.entries(msg.items).map(([cat, list]) => `${cat}: ${list.join(", ")}`);
  $("items").textContent = items.length ? "Other items - " + items.join(" | ") : "";
}
