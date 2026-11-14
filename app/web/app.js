// CareVoice: the call screen and the voices screen. Plain JavaScript, no build step.
import { listenOnDevice, matchesPrompt, readingPrompt } from "./speech-check.js";
import { toWav } from "./wav.js";

const $ = (selector, root = document) => root.querySelector(selector);

const EVENTS_TOPIC = "demo.voice_events"; // the worker's data topic
const MIN_SECONDS = 8; // a clone needs a clear sample: long enough to hear the voice, short enough to stay clean
const MAX_SECONDS = 25;
const READING_LANGUAGE = "en-US"; // the sentence to read is in English, so the device listens for English
const CONSENT_TEXT =
  "The speaker agrees that this demo stores this recording, encrypted, and uses their voice for its calls. " +
  "It is kept until the voice is deleted. They can withdraw this at any time by deleting the voice: the recording " +
  "is then deleted, and from any backup within 30 days.";

const state = {
  config: { languages: [], cloning: false },
  clones: [],
  mode: "gemini", // who speaks: "gemini" | "clone"
};

async function api(path, options = {}) {
  const response = await fetch(`/api${path}`, options);
  if (!response.ok) {
    let message = `Something went wrong (${response.status}).`;
    try { message = (await response.json()).detail || message; } catch { /* not JSON */ }
    throw new Error(typeof message === "string" ? message : JSON.stringify(message));
  }
  return response.json();
}

function show(element, text) {
  element.textContent = text || "";
  element.hidden = !text;
}

// ───────────────────────── tabs ─────────────────────────
function selectTab(name) {
  for (const tab of ["call", "voices"]) {
    $(`#tab-${tab}`).setAttribute("aria-selected", String(tab === name));
    $(`#panel-${tab}`).hidden = tab !== name;
  }
}
$("#tab-call").addEventListener("click", () => selectTab("call"));
$("#tab-voices").addEventListener("click", () => selectTab("voices"));

// ───────────────────────── the call ─────────────────────────
const call = { room: null, sessionId: null, timer: null, startedAt: 0, segments: new Map(), audio: [] };

const STATE_LABELS = { initializing: "Starting…", idle: "Ready", listening: "Listening", thinking: "Thinking…", speaking: "Speaking" };

function setAgentState(name) {
  const known = ["listening", "thinking", "speaking"].includes(name) ? name : "idle";
  $("#orb").dataset.state = known;
  $("#state").textContent = STATE_LABELS[name] || STATE_LABELS.idle;
}

function hasActiveClone() {
  return state.clones.some((clone) => clone.active && clone.status === "ready");
}

function renderMode() {
  const available = hasActiveClone();
  if (!available && state.mode === "clone") state.mode = "gemini";
  $("#mode-gemini").setAttribute("aria-checked", String(state.mode === "gemini"));
  $("#mode-clone").setAttribute("aria-checked", String(state.mode === "clone"));
  $("#mode-clone").disabled = !available || !!call.room;
  $("#mode-gemini").disabled = !!call.room;
  show($("#mode-hint"), available
    ? (state.mode === "clone"
      ? "Gemini hears and thinks. Its words are spoken by your cloned voice."
      : "Gemini hears, thinks and speaks with its own voice.")
    : "No cloned voice is in use yet. Add one in Voices and press “Use this voice”.");
}
$("#mode-gemini").addEventListener("click", () => { state.mode = "gemini"; renderMode(); });
$("#mode-clone").addEventListener("click", () => { state.mode = "clone"; renderMode(); });

function tick() {
  const seconds = Math.floor((Date.now() - call.startedAt) / 1000);
  $("#timer").textContent = `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}

function addTurn(id, role, content, { final = true, interrupted = false } = {}) {
  let item = call.segments.get(id);
  if (!item) {
    item = document.createElement("li");
    const who = document.createElement("span");
    who.className = "who";
    who.textContent = role === "user" ? "You" : "Assistant";
    const words = document.createElement("span");
    words.className = "words";
    item.append(who, words);
    $("#transcript").append(item);
    call.segments.set(id, item);
    $("#transcript-empty").hidden = true;
  }
  item.className = `turn ${role === "user" ? "user" : "agent"}${final ? "" : " interim"}${interrupted ? " cut" : ""}`;
  $(".words", item).textContent = content;
  $("#transcript").scrollTop = $("#transcript").scrollHeight;
}

function addToolCall(name, args, answer) {
  const item = document.createElement("li");
  item.className = "tool";
  const title = document.createElement("div");
  const label = document.createElement("span");
  label.className = "name";
  label.textContent = name;
  title.append(label);
  const argsEl = document.createElement("div");
  argsEl.className = "args";
  argsEl.textContent = JSON.stringify(args ?? {});
  const answerEl = document.createElement("div");
  answerEl.className = "answer";
  answerEl.textContent = answer;
  item.append(title, argsEl, answerEl);
  $("#tools").prepend(item);
}

function onWorkerEvent(event) {
  switch (event.type) {
    case "transcription.segment":
      addTurn(event.id, event.role, event.content, { final: event.final, interrupted: !!event.interrupted });
      break;
    case "agent.state.changed":
      setAgentState(event.state);
      break;
    case "tool.called":
      addToolCall(event.name, event.arguments, event.answer);
      break;
    case "voice.fallback":
      // The cloned voice failed: Gemini speaks with its own voice for the rest of the call.
      show($("#speaking-in"), "The cloned voice failed, so Gemini is speaking with its own voice for the rest of this call.");
      break;
    case "call.ended":
      void hangUp();
      break;
    case "error":
      show($("#call-error"), event.message || "The voice connection had a problem.");
      break;
    default:
      break;
  }
}

function resetCallScreen() {
  call.segments.clear();
  $("#transcript").replaceChildren();
  $("#tools").replaceChildren();
  $("#transcript-empty").hidden = false;
  show($("#call-error"), "");
}

async function startCall() {
  const button = $("#call-button");
  button.disabled = true;
  resetCallScreen();
  setAgentState("initializing");
  try {
    const session = await api("/session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ language: $("#call-language").value, use_clone: state.mode === "clone" }),
    });
    call.sessionId = session.session_id;
    const { Room, RoomEvent, Track } = window.LivekitClient;
    const room = new Room({ adaptiveStream: true, dynacast: true });
    call.room = room;
    room.on(RoomEvent.TrackSubscribed, (track) => {
      // The worker's voice: attach it to the page so it plays.
      if (track.kind === Track.Kind.Audio) {
        const element = track.attach();
        document.body.append(element);
        call.audio.push(element);
      }
    });
    room.on(RoomEvent.DataReceived, (payload, _participant, _kind, topic) => {
      if (topic !== EVENTS_TOPIC) return;
      try { onWorkerEvent(JSON.parse(new TextDecoder().decode(payload))); } catch { /* not ours */ }
    });
    room.on(RoomEvent.Disconnected, () => { if (call.room === room) void hangUp(); });
    await room.connect(session.server_url, session.participant_token);
    await room.localParticipant.setMicrophoneEnabled(true);

    show($("#speaking-in"), session.company_voice ? "Speaking in your cloned voice." : "Speaking with Gemini's own voice.");
    call.startedAt = Date.now();
    tick();
    call.timer = window.setInterval(tick, 1000);
    button.textContent = "Hang up";
    button.classList.add("hangup");
    setAgentState("listening");
  } catch (error) {
    console.error("[call] could not start", error);
    show($("#call-error"), error instanceof Error ? error.message : "The call could not start.");
    await hangUp();
  } finally {
    button.disabled = false;
    renderMode();
  }
}

async function hangUp() {
  const { room, sessionId } = call;
  call.room = null;
  call.sessionId = null;
  window.clearInterval(call.timer);
  call.timer = null;
  for (const element of call.audio) element.remove();
  call.audio = [];
  if (room) { try { await room.disconnect(); } catch { /* already gone */ } }
  if (sessionId) {
    try { await fetch("/api/session/end", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ session_id: sessionId }) }); } catch { /* the room closes itself */ }
  }
  const button = $("#call-button");
  button.textContent = "Start call";
  button.classList.remove("hangup");
  $("#timer").textContent = "00:00";
  show($("#speaking-in"), "");
  setAgentState("idle");
  renderMode();
}

$("#call-button").addEventListener("click", () => { void (call.room ? hangUp() : startCall()); });
// A tab closed without hanging up: tell the API, so the room and the grant end now.
window.addEventListener("pagehide", () => {
  if (call.sessionId) navigator.sendBeacon("/api/session/end", new Blob([JSON.stringify({ session_id: call.sessionId })], { type: "application/json" }));
});

// ───────────────────────── the voices ─────────────────────────
const voice = {
  mode: "record", // "record" | "import"
  sample: null, // { blob, filename, source, seconds }
  heard: "",
  ticked: false,
  consent: false,
  recorder: null,
  timer: null,
  elapsed: 0,
  stopListening: null,
  previewUrl: null,
  busy: false,
};

const languageLabel = (code) => state.config.languages.find((l) => l.code === code)?.label ?? code;

function renderClones() {
  const list = $("#clone-list");
  list.replaceChildren();
  $("#clone-empty").hidden = state.clones.length > 0;
  for (const clone of state.clones) {
    const item = document.createElement("li");
    item.dataset.clone = clone.id;
    if (clone.active && clone.status === "ready") item.className = "active";

    const name = document.createElement("span");
    name.className = "name";
    name.append(document.createTextNode(clone.label));
    const badge = (text, kind = "") => { const b = document.createElement("span"); b.className = `badge ${kind}`.trim(); b.textContent = text; return b; };
    name.append(badge(languageLabel(clone.language)));
    if (clone.status === "ready" && clone.active) name.append(badge("In use", "on"));
    if (clone.status === "gone") name.append(badge("Lost", "warn"));
    if (clone.status === "deleting") name.append(badge("Being deleted"));
    item.append(name);

    const actions = document.createElement("span");
    actions.className = "actions";
    const button = (text, onClick, extra = "") => { const b = document.createElement("button"); b.type = "button"; b.className = `ghost ${extra}`.trim(); b.textContent = text; b.disabled = voice.busy; b.addEventListener("click", onClick); return b; };
    if (clone.status === "ready") {
      actions.append(clone.active
        ? button("Stop using", () => change(() => api(`/clones/${clone.id}/deactivate`, { method: "POST" })))
        : button("Use this voice", () => change(() => api(`/clones/${clone.id}/activate`, { method: "POST" }))));
    }
    const result = document.createElement("span");
    actions.append(button("Check", async () => {
      result.textContent = "…";
      try {
        const { works } = await api(`/clones/${clone.id}/check`);
        result.textContent = works ? "✓ works" : "✗ not found";
        result.className = works ? "check-yes" : "check-no";
      } catch (error) { result.textContent = "✗"; result.className = "check-no"; console.error(error); }
    }), result);
    actions.append(button("Delete", () => {
      if (window.confirm(`Delete the cloned voice “${clone.label}”? This cannot be undone.`)) void change(() => api(`/clones/${clone.id}`, { method: "DELETE" }));
    }, "danger"));
    item.append(actions);

    if (clone.status === "gone") {
      const note = document.createElement("p");
      note.className = "note";
      note.textContent = "Lost at the voice provider. It is made again from the kept recording; if that does not happen, record it again.";
      item.append(note);
    }
    if (clone.status === "deleting") {
      const note = document.createElement("p");
      note.className = "note";
      note.textContent = "This voice is being deleted at the voice provider. It is retried until it is gone.";
      item.append(note);
    }
    list.append(item);
  }
}

async function loadClones() {
  try { state.clones = await api("/clones"); } catch (error) { console.error("[voices] could not list", error); }
  renderClones();
  renderMode();
}

async function change(action) {
  voice.busy = true;
  show($("#clone-error"), "");
  renderClones();
  try { await action(); } catch (error) { show($("#clone-error"), error.message); }
  voice.busy = false;
  await loadClones();
}

function sentence() {
  return readingPrompt($("#clone-speaker").value);
}

function heardIt() {
  return voice.sample?.source === "recorded" && matchesPrompt(sentence(), voice.heard);
}

function renderForm() {
  const recorded = voice.sample?.source === "recorded";
  $("#sentence").textContent = sentence();
  $("#mode-record").className = `mode ${voice.mode === "record" ? "on" : "off"}`;
  $("#mode-import").className = `mode ${voice.mode === "import" ? "on" : "off"}`;
  $("#sample-chip").hidden = !voice.sample;
  $("#sample-name").textContent = voice.sample ? (recorded ? `Your recording (${voice.sample.seconds} s)` : voice.sample.filename) : "";
  const preview = $("#sample-preview");
  preview.hidden = !voice.sample;
  $("#tick").hidden = !(recorded && !heardIt());
  $("#consent").hidden = !voice.sample;
  $("#consent-text").textContent = CONSENT_TEXT;
  $("#record-button").textContent = voice.recorder ? "Stop" : (recorded ? "Record again" : "Record");
  $("#record-button").prepend(...(voice.recorder ? [Object.assign(document.createElement("span"), { className: "recording-dot" })] : []));

  const readingOk = !recorded || heardIt() || voice.ticked;
  const missing = [
    !$("#clone-label").value.trim() && "a name for the voice",
    !$("#clone-speaker").value.trim() && "who speaks",
    !voice.sample && "a recording or a file",
    voice.sample && recorded && !heardIt() && !voice.ticked && "the tick that you read the sentence aloud",
    voice.sample && !voice.consent && "the speaker's consent",
  ].filter(Boolean);
  $("#make-button").disabled = voice.busy || missing.length > 0 || !readingOk;
  $("#still-needed").textContent = missing.length ? `Still needed: ${missing.join(", ")}.` : "";
}

function setSample(sample) {
  if (voice.previewUrl) URL.revokeObjectURL(voice.previewUrl);
  voice.sample = sample;
  voice.heard = "";
  voice.ticked = false;
  voice.consent = false;
  $("#tick-box").checked = false;
  $("#consent-box").checked = false;
  voice.previewUrl = sample ? URL.createObjectURL(sample.blob) : null;
  $("#sample-preview").src = voice.previewUrl || "";
  renderForm();
}

function stopTimer() {
  if (voice.timer !== null) { window.clearInterval(voice.timer); voice.timer = null; }
}

function stopRecording() {
  stopTimer();
  voice.stopListening?.();
  voice.stopListening = null;
  voice.recorder?.stop();
}

async function startRecording() {
  show($("#clone-error"), "");
  setSample(null);
  voice.elapsed = 0;
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: false, noiseSuppression: true } });
    const chunks = [];
    const recorder = new MediaRecorder(stream);
    recorder.ondataavailable = (event) => { if (event.data.size) chunks.push(event.data); };
    recorder.onstop = () => {
      stream.getTracks().forEach((track) => track.stop());
      voice.recorder = null;
      $("#record-timer").hidden = true;
      if (voice.elapsed < MIN_SECONDS) {
        show($("#clone-error"), `Too short: record at least ${MIN_SECONDS} seconds.`);
        renderForm();
        return;
      }
      const heard = voice.heard;
      setSample({ blob: new Blob(chunks, { type: recorder.mimeType }), filename: "recording.wav", source: "recorded", seconds: voice.elapsed });
      voice.heard = heard;
      renderForm();
    };
    voice.recorder = recorder;
    recorder.start();
    $("#record-timer").hidden = false;
    $("#record-timer").textContent = "0 s";
    voice.timer = window.setInterval(() => {
      voice.elapsed += 1;
      $("#record-timer").textContent = `${voice.elapsed} s`;
      if (voice.elapsed >= MAX_SECONDS) stopRecording();
    }, 1000);
    renderForm();
    // On the device only; a browser that cannot do that returns null and the speaker confirms by hand.
    voice.stopListening = await listenOnDevice(READING_LANGUAGE, (text) => { voice.heard = text; });
  } catch (error) {
    console.error("[voices] microphone failed", error);
    show($("#clone-error"), "The microphone could not be used. Allow it in the browser and try again.");
    voice.recorder = null;
    renderForm();
  }
}

$("#record-button").addEventListener("click", () => { void (voice.recorder ? stopRecording() : startRecording()); });
$("#import-file").addEventListener("change", (event) => {
  const file = event.target.files?.[0];
  if (!file) return; // closing the dialog without a file keeps what is in use
  setSample({ blob: file, filename: file.name, source: "imported" });
});
$("#sample-remove").addEventListener("click", () => { $("#import-file").value = ""; setSample(null); });
for (const radio of document.querySelectorAll('input[name="how"]')) {
  radio.addEventListener("change", () => {
    if (voice.recorder) { radio.checked = false; $('input[name="how"][value="record"]').checked = true; return; }
    voice.mode = radio.value;
    $("#import-file").value = "";
    setSample(null);
  });
}
$("#tick-box").addEventListener("change", (event) => { voice.ticked = event.target.checked; renderForm(); });
$("#consent-box").addEventListener("change", (event) => { voice.consent = event.target.checked; renderForm(); });
for (const id of ["#clone-label", "#clone-speaker"]) $(id).addEventListener("input", () => { renderForm(); });

$("#clone-form").addEventListener("submit", (event) => {
  event.preventDefault();
  if (!voice.sample) return;
  const recorded = voice.sample.source === "recorded";
  void change(async () => {
    const file = recorded ? new File([await toWav(voice.sample.blob)], voice.sample.filename, { type: "audio/wav" }) : voice.sample.blob;
    const body = new FormData();
    body.set("label", $("#clone-label").value.trim());
    body.set("language", $("#clone-language").value);
    body.set("speaker_name", $("#clone-speaker").value.trim());
    body.set("consent", String(voice.consent));
    body.set("source", voice.sample.source);
    body.set("reading_check", !recorded ? "" : heardIt() ? "heard" : "ticked");
    body.set("sample", file, voice.sample.filename);
    await api("/clones", { method: "POST", body });
    $("#clone-label").value = "";
    $("#clone-speaker").value = "";
    $("#import-file").value = "";
    setSample(null);
  });
});

// ───────────────────────── start ─────────────────────────
async function init() {
  try {
    state.config = await api("/config");
  } catch (error) {
    show($("#call-error"), "The API is not answering. Is it running?");
    console.error(error);
  }
  for (const select of [$("#call-language"), $("#clone-language")]) {
    select.replaceChildren(...state.config.languages.map((l) => Object.assign(document.createElement("option"), { value: l.code, textContent: l.label })));
  }
  $("#cloning-off").hidden = state.config.cloning;
  renderForm();
  await loadClones();
  setAgentState("idle");
}

init();
