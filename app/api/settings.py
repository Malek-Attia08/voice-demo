"""Settings, read from the environment each time they are asked for (so tests can change them)."""
import os


def get(name, default=None):
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def db_path():
    return get("DEMO_DB_PATH", "/data/demo.sqlite3")


# Mistral: text-to-speech and voice cloning (the only thing Mistral is used for in the demo).
def tts_api_key():
    return get("MISTRAL_API_KEY")


def tts_api_base():
    return get("TTS_API_BASE", "https://api.mistral.ai").rstrip("/")


def tts_model():
    return get("TTS_MODEL", "voxtral-mini-tts-3")


# How the cloned voice sounds (expressiveness 1.0 to 5.0, 0 sends none).
def clone_expressiveness():
    return float(get("TTS_CLONE_EXPRESSIVENESS", "3.0"))


def clone_pace():
    # Unset means "moderate", but an empty value means "send no pace tag".
    return os.environ.get("TTS_CLONE_PACE", "moderate").strip().lower()


def tts_max_chars():
    return int(get("TTS_MAX_CHARS", "4000"))


# VOICE_MASTER_KEYS: `id:base64 32-byte key` entries, newest first, comma separated.
def voice_master_keys():
    return get("VOICE_MASTER_KEYS", "")


# LiveKit: LIVEKIT_URL is where the API and the worker reach it, LIVEKIT_PUBLIC_URL where the browser does.
def livekit_url():
    return (get("LIVEKIT_URL") or "").rstrip("/") or None


def livekit_public_url():
    return (get("LIVEKIT_PUBLIC_URL") or "").rstrip("/") or livekit_url()


def livekit_key():
    return get("LIVEKIT_API_KEY")


def livekit_secret():
    return get("LIVEKIT_API_SECRET")


def speech_secret():
    """Signs the speech grants. Falls back to the LiveKit secret, which the API already holds."""
    return get("DEMO_SECRET") or livekit_secret() or "demo-secret-change-me"
