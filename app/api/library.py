"""The library of cloned voices.

Each clone is a row that keeps the id of the voice Mistral made from the recording, the language it was made for and
the consent of the person whose voice it is. One clone per language speaks it; a language without a clone of its own
is spoken by a clone that speaks another language.

The demo and Mistral are kept in step: a clone is deleted at Mistral first and here only after, and a delete Mistral
could not do is kept (status "deleting") and retried, so no voice is left at Mistral that the demo has forgotten.
"""
import asyncio
import base64
import logging
import os
import uuid
from datetime import datetime, timezone

from api import db, settings, speech, vault

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 90
ACCEPTED_EXTENSIONS = (".mp3", ".wav")
MIN_DAYS, MAX_DAYS = 1, 365
# The wording the speaker agreed to; a new text gets a new version (web/voices.js shows the same text).
CONSENT_WORDING = "demo-v1"
# Mistral is told to keep a voice this many days (its longest), and every night again: a voice lasts until it is
# deleted, and one Mistral loses anyway is made again from the kept recording.
RETENTION_DAYS = 365

READY, PENDING, DELETING, GONE = "ready", "pending", "deleting", "gone"


class VoiceLibraryError(Exception):
    """Something the user can be told: the message is safe to show."""


class ConsentRequired(VoiceLibraryError):
    pass


class UnsupportedSample(VoiceLibraryError):
    pass


class CloneError(VoiceLibraryError):
    pass


class MistralUnavailable(VoiceLibraryError):
    pass


class NotFound(VoiceLibraryError):
    pass


def _headers():
    return {"Authorization": f"Bearer {settings.tts_api_key()}"}


def _voices_url(voice_id=""):
    return f"{settings.tts_api_base()}/v1/audio/voices" + (f"/{voice_id}" if voice_id else "")


def _get(clone_id):
    row = db.one("SELECT * FROM voice_clones WHERE id = ?", (clone_id,))
    if row is None:
        raise NotFound("This voice does not exist.")
    return dict(row)


def list_clones():
    """The clones, oldest first."""
    return [dict(row) for row in db.many("SELECT * FROM voice_clones ORDER BY created_at, rowid")]


def _base(language):
    return (language or "").lower().split("-")[0]


def active_voice_id(language=None):
    """The Mistral voice id that speaks `language` (a full code, be-nl), or None.

    The clone made for exactly that language first, then one made for the same base language (nl-nl is served by
    be-nl), then the active clone that has been speaking the longest; None when no usable clone is active."""
    active = [dict(row) for row in db.many(
        "SELECT * FROM voice_clones WHERE active = 1 AND status = ? ORDER BY created_at, rowid", (READY,))]
    if not active:
        return None
    if language:
        wanted = language.lower()
        for clone in active:
            if clone["language"].lower() == wanted:
                return clone["mistral_voice_id"]
        for clone in active:
            if _base(clone["language"]) == _base(wanted):
                return clone["mistral_voice_id"]
    return active[0]["mistral_voice_id"]


def activate(clone_id):
    """Make this clone the one that speaks its language; the one that did stops."""
    clone = _get(clone_id)
    if clone["status"] != READY:
        raise VoiceLibraryError("This voice cannot be used.")
    conn = db.connect()
    try:
        with conn:
            conn.execute("UPDATE voice_clones SET active = 0 WHERE language = ? AND active = 1 AND id != ?",
                         (clone["language"], clone_id))
            conn.execute("UPDATE voice_clones SET active = 1 WHERE id = ?", (clone_id,))
    finally:
        conn.close()


def deactivate(clone_id):
    """This clone stops speaking: its language is spoken by another clone, or by Gemini's own voice."""
    _get(clone_id)
    db.run("UPDATE voice_clones SET active = 0 WHERE id = ?", (clone_id,))


def _clean_language(language):
    language = (language or "").strip().lower()
    if not language or len(language) > 20:
        raise VoiceLibraryError("Choose the language of the voice.")
    return language


def _check_sample(sample, filename, speaker_name, consent):
    if not consent or not (speaker_name or "").strip():
        raise ConsentRequired("The speaker's name and consent are needed.")
    if not sample:
        raise CloneError("There is no recording.")
    if os.path.splitext(filename or "")[1].lower() not in ACCEPTED_EXTENSIONS:
        raise UnsupportedSample("Use an mp3 or wav recording.")
    if not settings.tts_api_key():
        raise CloneError("Voice cloning is not set up.")


async def _create_at_mistral(clone, sample, filename):
    import httpx

    body = {"name": f"voicedemo:{clone['id']}", "sample_audio": base64.b64encode(sample).decode(),
            "sample_filename": filename, "languages": [_base(clone["language"])],
            "retention_notice": clone["retention_days"]}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            response = await client.post(_voices_url(), json=body, headers=_headers())
    except httpx.HTTPError as exc:
        logger.warning("Voice cloning failed: %s", exc)
        raise CloneError("The voice service could not be reached.") from exc
    if response.status_code >= 400:
        logger.warning("Voice cloning answered HTTP %s: %s", response.status_code, response.text[:500])
        raise CloneError("The voice service could not clone this recording.")
    try:
        return response.json()["id"]
    except (ValueError, KeyError, TypeError) as exc:
        raise CloneError("The voice service sent no voice.") from exc


async def add(label, sample, filename, *, language, speaker_name, consent, source="imported", reading_check=""):
    """Clone `sample` (mp3 or wav bytes) with Mistral and keep the voice in the library.

    The speaker's name and consent are required. The row exists before the voice does, so the voice at Mistral is
    named after it; a voice Mistral refuses leaves no row."""
    language = _clean_language(language)
    _check_sample(sample, filename, speaker_name, consent)
    clone_id = uuid.uuid4().hex
    db.run("INSERT INTO voice_clones (id, label, language, status, speaker_name, consented_at, consent_wording, "
           "source, reading_check, retention_days) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
           (clone_id, label, language, PENDING, speaker_name.strip(), datetime.now(timezone.utc).isoformat(),
            CONSENT_WORDING, source, reading_check, RETENTION_DAYS))
    clone = _get(clone_id)
    try:
        vault.store(clone_id, sample, filename)
        voice_id = await _create_at_mistral(clone, sample, filename)
    except vault.VaultNotConfigured as exc:
        db.run("DELETE FROM voice_clones WHERE id = ?", (clone_id,))
        raise VoiceLibraryError("Voice recordings cannot be kept here yet, so no voice can be made.") from exc
    except VoiceLibraryError:
        db.run("DELETE FROM voice_clones WHERE id = ?", (clone_id,))
        raise
    db.run("UPDATE voice_clones SET mistral_voice_id = ?, status = ? WHERE id = ?", (voice_id, READY, clone_id))
    logger.info("Voice %s cloned (%d bytes of recording)", clone_id, len(sample))
    return _get(clone_id)


async def _delete_at_mistral(voice_id):
    """Delete the voice at Mistral; a voice Mistral does not have counts as deleted. Raises MistralUnavailable."""
    import httpx

    if not voice_id:
        return
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            response = await client.delete(_voices_url(voice_id), headers=_headers())
    except httpx.HTTPError as exc:
        logger.warning("Mistral could not be reached to delete a voice: %s", exc)
        raise MistralUnavailable("The voice service could not be reached. The voice will be deleted later.") from exc
    if response.status_code == 404:
        return
    if response.status_code >= 400:
        logger.warning("Mistral could not delete a voice (HTTP %s): %s", response.status_code, response.text[:300])
        raise MistralUnavailable("The voice service could not delete the voice. It will be deleted later.")


async def _remove(clone):
    """Delete `clone` at Mistral and then here; if Mistral cannot, keep it marked "deleting"."""
    db.run("UPDATE voice_clones SET status = ?, active = 0 WHERE id = ?", (DELETING, clone["id"]))
    await _delete_at_mistral(clone["mistral_voice_id"])
    db.run("DELETE FROM voice_clones WHERE id = ?", (clone["id"],))
    logger.info("Voice %s deleted", clone["id"])


async def delete(clone_id):
    """Delete the clone at Mistral first, then here (its recording goes with it)."""
    await _remove(_get(clone_id))


async def retry_pending_deletes():
    """Delete again the clones Mistral could not delete; returns how many are now gone."""
    done = 0
    for row in db.many("SELECT * FROM voice_clones WHERE status = ?", (DELETING,)):
        try:
            await _remove(dict(row))
            done += 1
        except MistralUnavailable:
            pass
    return done


async def replace(clone_id, sample, filename, *, label=None, language=None, **consent):
    """Make a new clone from `sample` (for the old one's language unless told otherwise), let it speak if the old one
    did, then delete the old one at Mistral. If Mistral cannot delete the old voice yet, the new one works anyway."""
    old = _get(clone_id)
    new = await add(label or old["label"], sample, filename, language=language or old["language"], **consent)
    if old["active"]:
        activate(new["id"])
    try:
        await _remove(old)
    except MistralUnavailable:
        pass
    return _get(new["id"])


async def set_retention(clone_id, days):
    """Tell Mistral how many days to keep the voice, and keep it here."""
    import httpx

    if isinstance(days, bool) or not isinstance(days, int) or not MIN_DAYS <= days <= MAX_DAYS:
        raise ValueError(f"Days must be a whole number from {MIN_DAYS} to {MAX_DAYS}.")
    clone = _get(clone_id)
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            response = await client.patch(_voices_url(clone["mistral_voice_id"]), json={"retention_notice": days},
                                          headers=_headers())
    except httpx.HTTPError as exc:
        raise MistralUnavailable("The voice service could not be reached.") from exc
    if response.status_code >= 400:
        raise MistralUnavailable("The voice service did not accept the change.")
    db.run("UPDATE voice_clones SET retention_days = ? WHERE id = ?", (days, clone_id))


async def renew_retention():
    """Tell Mistral again to keep every ready voice for RETENTION_DAYS; returns how many it accepted."""
    renewed = 0
    for row in db.many("SELECT id FROM voice_clones WHERE status = ?", (READY,)):
        try:
            await set_retention(row["id"], RETENTION_DAYS)
            renewed += 1
        except VoiceLibraryError as exc:
            logger.warning("Voice %s could not be renewed: %s", row["id"], exc)
    return renewed


async def check(clone_id):
    """Whether Mistral still has the voice. A voice it has lost is marked "gone" and stops speaking."""
    import httpx

    clone = _get(clone_id)
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            response = await client.get(_voices_url(clone["mistral_voice_id"]), headers=_headers())
    except httpx.HTTPError as exc:
        raise MistralUnavailable("The voice service could not be reached.") from exc
    if response.status_code == 404:
        mark_gone(clone_id)
        return False
    if response.status_code >= 400:
        raise MistralUnavailable("The voice service could not say.")
    return True


def mark_gone(clone_id):
    """Mistral no longer has this voice: it stops speaking, and stays in the library to be made again from its
    kept recording."""
    db.run("UPDATE voice_clones SET status = ? WHERE id = ?", (GONE, clone_id))
    logger.warning("Voice %s is gone at Mistral", clone_id)


async def heal(clone_id):
    """Make a voice Mistral has lost again, from the recording kept with the clone."""
    clone = _get(clone_id)
    if clone["status"] == READY:
        return clone
    if clone["status"] != GONE:
        raise VoiceLibraryError("This voice cannot be made again.")

    # Only one repair may start: a call that finds the voice lost and the nightly care can arrive together, and two
    # repairs would leave a voice at Mistral that the demo does not know.
    if not db.run("UPDATE voice_clones SET status = ? WHERE id = ? AND status = ?", (PENDING, clone_id, GONE)):
        return _get(clone_id)
    try:
        try:
            audio, filename = vault.load(clone_id), vault.filename_of(clone_id)
        except vault.VaultError as exc:
            raise VoiceLibraryError("The recording of this voice is not kept: record it again.") from exc
        voice_id = await _create_at_mistral(clone, audio, filename)
    except BaseException:
        db.run("UPDATE voice_clones SET status = ? WHERE id = ?", (GONE, clone_id))
        raise
    db.run("UPDATE voice_clones SET mistral_voice_id = ?, status = ? WHERE id = ?", (voice_id, READY, clone_id))
    logger.info("Voice %s made again", clone_id)
    return _get(clone_id)


_tasks = set()


def background_tasks():
    """The repairs running in the background (tests wait for them)."""
    return list(_tasks)


def _in_background(coroutine):
    task = asyncio.ensure_future(coroutine)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _heal_quietly(clone_id):
    try:
        await heal(clone_id)
    except VoiceLibraryError as exc:
        logger.warning("Voice %s could not be made again: %s", clone_id, exc)


def _clone_of(voice_id):
    row = db.one("SELECT * FROM voice_clones WHERE mistral_voice_id = ?", (voice_id,))
    return dict(row) if row else None


async def speak(text, language=None):
    """`text` spoken in `language` with the active clone for it, as mp3 audio; None when no clone speaks or it cannot.

    A voice Mistral turns out not to have is marked gone and made again in the background; this call gets None,
    so its caller falls back to another voice instead of staying silent."""
    voice_id = active_voice_id(language)
    if not voice_id:
        return None
    try:
        audio = await speech.speak_voice(text, voice_id)
    except speech.VoiceMissing:
        clone = _clone_of(voice_id)
        if clone is not None:
            mark_gone(clone["id"])
            _in_background(_heal_quietly(clone["id"]))
        return None
    if audio:
        # Which clone spoke, under which consent, how much: never the words.
        used = _clone_of(voice_id)
        logger.info("Voice %s (consent %s) spoke: %d bytes for %d characters", used["id"] if used else "unknown",
                    used["consent_wording"] if used else "unknown", len(audio), len(text))
    return audio


async def verified_voice_id(language=None):
    """The active voice for `language`, checked at Mistral just now; None when there is none or it cannot be used.

    A call is only promised the cloned voice when it will really speak: a voice Mistral has lost is made again in the
    background (this call gets None, so Gemini speaks with its own voice from the start), and a Mistral that cannot
    say leaves the voice as it is."""
    voice_id = active_voice_id(language)
    clone = _clone_of(voice_id) if voice_id else None
    if clone is None:
        return None
    try:
        present = await check(clone["id"])
    except MistralUnavailable:
        return None
    if present:
        return voice_id
    _in_background(_heal_quietly(clone["id"]))
    return None


async def check_all():
    """The daily check: every ready voice is looked up at Mistral, and those it has lost are made again."""
    report = {"checked": 0, "lost": 0, "healed": 0}
    for row in db.many("SELECT id FROM voice_clones WHERE status = ?", (READY,)):
        try:
            present = await check(row["id"])
        except MistralUnavailable:
            continue
        report["checked"] += 1
        if present:
            continue
        report["lost"] += 1
        try:
            await heal(row["id"])
            report["healed"] += 1
        except VoiceLibraryError as exc:
            logger.warning("Voice %s could not be made again: %s", row["id"], exc)
    return report
