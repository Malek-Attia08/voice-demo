
# CareVoice: a medical voice assistant with a cloned voice

CareVoice lets a patient talk to **Mira**, the voice assistant of a clinic, on a call. **Gemini Live** hears and thinks.
Then **you choose who speaks**:

- **Gemini's voice**: Gemini answers out loud with its own voice.
- **A cloned voice**: Gemini's words are spoken by a voice cloned from a short recording (Mistral text-to-speech), for
  example the voice of a familiar nurse or doctor, recorded with their consent.

Mira is not a doctor and never diagnoses. She has five **tools** (`rag_answer` for the clinic's
appointments, hours, prescriptions and contact; `medical_info` for general health information that always puts an
emergency first; `history`, `get_time`, `find_appointment_slots` for free appointment slots), so you can see tool calls happen without any real backend.
Nothing here is medical advice.

---

## 1. What is in the folder

```
voice-demo
├── README.md                this file
└── app\                     THE APP
    ├── .env.example         every setting, explained      (copy to .env)
    ├── .env                 your settings (secrets)       (never commit)
    ├── docker-compose.yml   livekit + api + worker in one command
    ├── docker\livekit.yaml  the LiveKit server's config
    ├── api\                 FastAPI: the page, call setup, voice library, vault, speech route, nightly care
    ├── worker\              the LiveKit agent: Gemini Live + tools + cloned voice
    ├── web\                 the page (plain HTML/CSS/JS, no build step)
    ├── scripts\             start.ps1 / stop.ps1 (everything), setup, run-api, run-worker, run-livekit, test
    └── docs\                architecture.md, cloning.md, troubleshooting.md

```

## 2. What you need

| Need | For | Where to get it |
|---|---|---|
| A **Gemini API key** (`GOOGLE_API_KEY`) | every call (Gemini hears and thinks) | Google AI Studio |
| A **Mistral API key** (`MISTRAL_API_KEY`) | cloning a voice and speaking with it. Optional: without it the demo works with Gemini's voice only | console.mistral.ai |
| **Python 3.11 or newer** | running without Docker | python.org |
| **Docker Desktop** | the LiveKit server (always), and optionally the API and worker | docker.com |
| A **microphone** and Chrome or Edge | talking, and recording a voice sample | |

The page loads one file from the internet: LiveKit's browser client from `cdn.jsdelivr.net`, pinned to an exact version.

## 3. Run it

### Easiest: one command

```powershell
cd voice-demo\app
.\scripts\start.ps1          # first run also does the setup; opens http://localhost:8000
.\scripts\stop.ps1           # stops it
```

Put `GOOGLE_API_KEY` (and `MISTRAL_API_KEY` for cloning) in `app\.env` first. The script starts Docker Desktop if it is
not running, then LiveKit, the API and the worker (logs in `app\data\logs\`). It was syntax-checked but **not run** here.
The two options below are the manual versions of the same thing.

### A. Everything in Docker (one command)

```powershell
cd voice-demo\app
copy .env.example .env        # already done for you: .env exists with a master key
# put GOOGLE_API_KEY (and MISTRAL_API_KEY for cloning) in .env
docker compose up --build
```

Open **http://localhost:8000**.

### B. LiveKit in Docker, the API and the worker from Python (faster to change)

Docker is then needed for the LiveKit server only.

```powershell
cd voice-demo\app
.\scripts\setup.ps1          # once: .env, a master key, .venv-api and .venv-worker
# put GOOGLE_API_KEY (and MISTRAL_API_KEY) in .env

.\scripts\run-livekit.ps1    # terminal 1  (Docker Desktop must be running)
.\scripts\run-api.ps1        # terminal 2  -> http://localhost:8000
.\scripts\run-worker.ps1     # terminal 3
```

The API must listen on port **8000**: LiveKit's webhook (`docker/livekit.yaml`) points there.

### Try it

1. **Call** tab: pick a language, leave "Gemini's voice", press **Start call**, allow the microphone, say hello.
   Ask "how do I cancel an appointment?", "I have a fever, what should I do?", "what time is it?", "what appointments does cardiology have?", "what did I just ask?". The **Tool calls**
   panel shows each tool Gemini used and what it returned.
2. **Voices** tab: choose the language, name the voice, say who speaks, then either **Record** (read the shown
   sentence aloud, 8 to 25 seconds) or **import** an mp3/wav. Tick consent, press **Make the voice**, then
   **Use this voice**.
3. Back on **Call**: switch **Who speaks** to **Cloned voice** and start a call. Gemini's words now come in the clone.

## 4. Tests

No network, no keys, no LiveKit server needed.

```powershell
cd voice-demo\app
.\scripts\test.ps1           # 108 API tests + 84 worker tests
```

(`test.ps1 -ApiPython ... -WorkerPython ...` points it at other Python environments.)

## 5. What was checked, and what was not

Checked (automatically or by hand while building):

- All 192 tests pass. The vault, the voice library, the speech tuning, the call setup, the speech grant, the nightly
  care and the worker's logic are covered, against fakes of Mistral and LiveKit.
- The page loads with a clean console, works at phone width, and the whole Voices flow (import, consent, make, use)
  ran in a browser against the real API and a stand-in Mistral.

**Not checked** (needs your keys, hardware or Docker, none of which was available here):

- A real call: Gemini Live with the real model, a real LiveKit server, a real microphone.
- Real Mistral cloning and speech.
- `docker compose up --build` (the images were never built here).
- Whether the muted-Gemini path stays in sync with LiveKit's turn-taking on a real call. It is untested
  logic and was never run against Google here.

If a first call fails, `docs/troubleshooting.md` lists the likely causes in order.

## 6. Safety notes

- `.env` holds secrets. It is in `.gitignore`. Never share one `.env` between environments: each makes its own master key.
- The **worker never holds the Mistral key or the master key**. It gets a per-call *speech grant* and asks the API to
  speak. The grant stops working when the call ends.
- The browser's LiveKit token lets it into one room, publish its microphone, and nothing else. It is valid 10 minutes to
  join; a call lasts at most 30 minutes.
- Recordings are stored **encrypted**; the master key is not in the database. See `docs/cloning.md`.
- Only record a voice with the speaker's consent. The page asks for it and stores it with the voice.

## 7. More

- `app/docs/architecture.md`: the pieces, the flows, every endpoint and setting.
- `app/docs/cloning.md`: what happens to a voice from recording to deletion.
- `app/docs/troubleshooting.md`: when something does not work.
