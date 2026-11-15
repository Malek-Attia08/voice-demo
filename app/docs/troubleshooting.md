# Troubleshooting

Check in this order. Most problems are the first three.

## "Voice is not set up" when starting a call
The API has no LiveKit settings. In `.env`: `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` must be set (the
defaults in `.env.example` work with `docker/livekit.yaml`). Restart the API after editing `.env`: it reads it at start.

## "The voice call could not start"
The room could not be made or the worker could not be dispatched.
- Is the **LiveKit server** running? (`scripts\start.ps1`, `scripts\run-livekit.ps1` or `docker compose up livekit`;
  Docker Desktop must be running.) The API logs `The voice worker could not be dispatched: ...` with the reason.
  After `start.ps1` the logs are in `data\logs\` (`api.err.log`, `worker.err.log`: the programs log to stderr).
- The key and secret in `.env` must **match `docker/livekit.yaml`** exactly.

## The call starts but the assistant never answers, or hangs on "Starting…"
- Is the **worker** running, and does its log say it registered with LiveKit? No worker means nobody answers the
  dispatch. (`scripts\run-worker.ps1` or the `worker` container.)
- Is `GOOGLE_API_KEY` set, and does the key allow the Gemini Live model? Check `GEMINI_LIVE_MODEL` (default
  `gemini-3.8-live`): a model your key cannot use fails when the worker opens the Gemini session. The worker logs the
  *kind* of error only (never its text, which could carry the key).
- Did the browser allow the **microphone**? Look for the permission icon in the address bar.

## You hear nothing at all
- The page needs one file from the internet (`livekit-client` from `cdn.jsdelivr.net`). If it is blocked, the console
  shows `LivekitClient is undefined` or similar. Open the browser's developer console.
- Some browsers block audio until you click on the page. You did click Start call, so this is rare; check the tab is not
  muted.
- Media travels on UDP 7882 and TCP 7881. A firewall or VPN can block them; try without the VPN.

## The cloned voice does not speak, Gemini's does
If the page says "The cloned voice failed, so Gemini is speaking with its own voice", that is the fallback working: the
clone could not speak a sentence (Mistral down, the voice lost, a wrong key). The API log says why.
Otherwise check these:
- The line under the Start call button says who speaks once connected: "Speaking in your cloned voice" or "Speaking with Gemini's own voice".
  If it says Gemini's, the call had no grant: no clone is **active** for the language. Voices tab, **Use this voice**.
- **Who speaks** is set to "Gemini's voice". Switch it to "Cloned voice" before you start the call. The setting is fixed
  per call.
- The clone is `gone` or `deleting`: only `ready` clones speak.
- `MISTRAL_API_KEY` is wrong or has no access to voices. The API logs `Text to speech answered HTTP ...` with Mistral's
  reason.
- The worker cannot reach the API: `DEMO_API_URL` must be where the worker sees the API (`http://api:8000` inside
  Docker compose, `http://localhost:8000` from the venvs). The worker logs `DEMO_API_URL is not set` if it is empty.

## The cloned voice sounds flat, or not like the speaker
A clone copies the delivery of its recording. Record again, in a quiet room, in a natural voice. Then try
`TTS_CLONE_EXPRESSIVENESS` (1.0 to 5.0): higher is livelier but less like the speaker. `TTS_CLONE_PACE` is documented
for English only.

## "Voice cloning is not set up" / the Voices screen says cloning is off
`MISTRAL_API_KEY` is empty. Set it and restart the API.

## "Voice recordings cannot be kept here yet, so no voice can be made"
`VOICE_MASTER_KEYS` is empty or malformed. Run `python -m api.make_master_key .env`. The format is
`id:base64 32-byte key`; a key of any other length is refused.

## "The voice service could not clone this recording"
Mistral refused the sample: too short, too noisy, or the wrong format. The API logs Mistral's answer. Try a clean
10 to 20 second wav.

## A recording or key error after changing `.env`
Opening a recording needs the master key that locked it. If you replaced the key instead of rotating, old recordings no
longer open ("The key that locked this recording is no longer held"). Put the old key back at the end of
`VOICE_MASTER_KEYS`. Use `--rotate`, never edit the key by hand.

## The call does not end when I close the tab
The page tells the API on `pagehide`, and LiveKit's webhook ends the call when its room closes. If the webhook cannot
reach the API (it points at `host.docker.internal:8000`), the call still ends on its own: 2 minutes after everyone
left, or at 30 minutes at the latest. The API must listen on port 8000.

## Starting over
- Delete the database: stop the API, delete `app\data\demo.sqlite3` (venv run) or `docker compose down -v` (Docker). This
  deletes the demo's rows and recordings, **not the voices at Mistral**: delete those in the Voices tab first.
- Run the tests to check the install: `scripts\test.ps1`.
