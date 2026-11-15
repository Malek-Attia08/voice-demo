"""Speaking text in a cloned voice.

Speech comes from Mistral's Voxtral TTS (POST /v1/audio/speech), which answers JSON with the audio base64-encoded.
Gemini's words can carry Markdown, so they are turned into plain spoken text first.
"""
import base64
import logging
import re

from api import settings

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 60
PACES = ("very-slow", "slow", "moderate", "fast", "very-fast")
EXPRESSIVENESS_RANGE = (1.0, 5.0)


class VoiceMissing(Exception):
    """Mistral does not have the voice any more (HTTP 404)."""


def enabled():
    return bool(settings.tts_api_key())


_CODE_BLOCK = re.compile(r"```.*?(```|$)", re.S)
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_CITATION = re.compile(r"\s*\[(?:S\d+(?:\s*[,;]\s*S\d+)*)\]")
_URL = re.compile(r"https?://\S+")
_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_LINE_MARK = re.compile(r"^\s{0,3}(?:#{1,6}\s+|>\s?|[-*+]\s+|\d+[.)]\s+)")
_EMPHASIS = re.compile(r"(\*\*|__|\*|~~|`)(?=\S)(.+?)(?<=\S)\1")


def spoken_text(markdown, limit=None):
    """The text as it should be heard: plain sentences, cut at a sentence end within `limit` characters."""
    limit = limit or settings.tts_max_chars()
    text = _CODE_BLOCK.sub(" ", markdown or "")
    text = _IMAGE.sub("", text)
    text = _LINK.sub(r"\1", text)
    text = _CITATION.sub("", text)
    text = _URL.sub("", text)
    lines = []
    for line in text.splitlines():
        if _TABLE_RULE.match(line):
            continue
        line = _LINE_MARK.sub("", line)
        if "|" in line:
            line = ", ".join(cell.strip() for cell in line.strip().strip("|").split("|") if cell.strip())
        line = _EMPHASIS.sub(r"\2", line)
        line = line.replace("`", "").replace("*", "").strip()
        if not line:
            continue
        # A heading or list item ends without punctuation; give it a pause.
        lines.append(line if line[-1] in ".!?:;," else f"{line}.")
    text = " ".join(" ".join(lines).split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return cut[:end + 1] if end > limit // 2 else cut.rsplit(" ", 1)[0]


def _headers():
    return {"Authorization": f"Bearer {settings.tts_api_key()}"}


def clone_request(spoken, voice_id):
    """The speech request for a cloned voice: the chosen pace as a tag, the chosen expressiveness, each only when valid."""
    pace = settings.clone_pace()
    body = {"model": settings.tts_model(), "input": f"[pace={pace}]{spoken}" if pace in PACES else spoken,
            "response_format": "mp3", "voice_id": voice_id}
    low, high = EXPRESSIVENESS_RANGE
    if low <= settings.clone_expressiveness() <= high:
        body["expressiveness"] = settings.clone_expressiveness()
    return body


async def speak_voice(text, voice_id):
    """`text` (Markdown allowed) spoken with the Mistral voice `voice_id`, as mp3 audio; None when it cannot be.
    Raises VoiceMissing when Mistral has no such voice."""
    import httpx

    spoken = spoken_text(text)
    if not (enabled() and spoken and voice_id):
        return None
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            response = await client.post(f"{settings.tts_api_base()}/v1/audio/speech", headers=_headers(),
                                         json=clone_request(spoken, voice_id))
        if response.status_code == 404:
            raise VoiceMissing(voice_id)
        if response.status_code >= 400:
            logger.warning("Text to speech answered HTTP %s with the custom voice: %s", response.status_code,
                           response.text[:500])
            return None
        return base64.b64decode(response.json()["audio_data"])
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        logger.warning("Text to speech with the custom voice failed: %s", exc)
        return None
