"""The demo API and the web page: voice calls, the cloned-voice library, and the speech route the worker calls.

Run:  uvicorn api.main:app
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from api import calls, care, library, settings, speech

logger = logging.getLogger("voice-demo")

MAX_SAMPLE_BYTES = 15 * 1024 * 1024
WEB = Path(__file__).resolve().parent.parent / "web"


@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(care.loop())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title="Voice demo", lifespan=lifespan)


def _status_of(exc):
    if isinstance(exc, library.NotFound):
        return 404
    if isinstance(exc, (library.MistralUnavailable, library.CloneError)):
        return 502
    return 400


def _public(clone):
    """What the page sees of a clone: never the provider's voice id."""
    return {key: clone[key] for key in ("id", "label", "language", "status", "speaker_name", "consented_at",
                                        "consent_wording", "source", "reading_check", "retention_days", "created_at")
            } | {"active": bool(clone["active"])}


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/config")
def config():
    return {"languages": [{"code": code, "label": label} for code, label in calls.LANGUAGES.items()],
            "consent_wording": library.CONSENT_WORDING, "cloning": speech.enabled()}


@app.get("/api/clones")
def list_clones():
    return [_public(clone) for clone in library.list_clones()]


@app.post("/api/clones")
async def make_clone(label: str = Form(...), language: str = Form(...), speaker_name: str = Form(...),
                     consent: str = Form("false"), source: str = Form("imported"), reading_check: str = Form(""),
                     sample: UploadFile = File(...)):
    audio = await sample.read(MAX_SAMPLE_BYTES + 1)
    if len(audio) > MAX_SAMPLE_BYTES:
        raise HTTPException(413, "The recording must be under 15 MB.")
    try:
        clone = await library.add(label.strip(), audio, sample.filename, language=language, speaker_name=speaker_name,
                                  consent=consent.strip().lower() in ("true", "1", "on", "yes"), source=source,
                                  reading_check=reading_check)
    except library.VoiceLibraryError as exc:
        raise HTTPException(_status_of(exc), str(exc)) from exc
    return _public(clone)


@app.post("/api/clones/{clone_id}/activate")
def activate(clone_id: str):
    try:
        library.activate(clone_id)
    except library.VoiceLibraryError as exc:
        raise HTTPException(_status_of(exc), str(exc)) from exc
    return {"ok": True}


@app.post("/api/clones/{clone_id}/deactivate")
def deactivate(clone_id: str):
    try:
        library.deactivate(clone_id)
    except library.VoiceLibraryError as exc:
        raise HTTPException(_status_of(exc), str(exc)) from exc
    return {"ok": True}


@app.get("/api/clones/{clone_id}/check")
async def check(clone_id: str):
    try:
        return {"works": await library.check(clone_id)}
    except library.VoiceLibraryError as exc:
        raise HTTPException(_status_of(exc), str(exc)) from exc


@app.delete("/api/clones/{clone_id}")
async def delete(clone_id: str):
    try:
        await library.delete(clone_id)
    except library.VoiceLibraryError as exc:
        raise HTTPException(_status_of(exc), str(exc)) from exc
    return {"ok": True}


class SessionRequest(BaseModel):
    language: str = "en"
    use_clone: bool = True


class EndRequest(BaseModel):
    session_id: str


@app.post("/api/session")
async def start_session(request: SessionRequest):
    try:
        return await calls.create_session(request.language, request.use_clone)
    except calls.CallError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


@app.post("/api/session/end")
async def end_session(request: EndRequest):
    if not await calls.end_session(request.session_id):
        raise HTTPException(404, "There is no such call.")
    return {"ok": True}


@app.post("/api/livekit-webhook")
async def livekit_webhook(request: Request):
    event = calls.read_webhook((await request.body()).decode(), request.headers.get("Authorization"))
    if event is None:
        raise HTTPException(401, "Not a LiveKit event.")
    call_id = calls.finished_call(event)
    if call_id:
        calls.finish_call(call_id)
    return {"ok": True}


class SpeechRequest(BaseModel):
    grant: str
    text: str


@app.post("/api/speech")
async def speak(request: SpeechRequest):
    """What the worker calls for each sentence the cloned voice says. The grant is the call's own."""
    found = calls.read_speech_grant(request.grant)
    if found is None:
        raise HTTPException(403, "This call may not use the voice.")
    if not speech.spoken_text(request.text):
        raise HTTPException(400, "There is nothing to read aloud.")
    audio = await library.speak(request.text, found["language"])
    if audio is None:
        raise HTTPException(404, "No cloned voice is speaking.")
    return Response(audio, media_type="audio/mpeg")


if WEB.is_dir():
    app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
