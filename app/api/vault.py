"""The vault that keeps the recordings the cloned voices were made from.

Every recording is encrypted with AES-256-GCM under a key of its own. That key is stored with it, locked by a master
key; the master key is not in the database (VOICE_MASTER_KEYS). Both are bound to the clone they belong to, so a
recording moved to another clone does not open. Several master keys can be configured, the newest first: that is how a
key is rotated (rewrap_all), and a retired key opens nothing. Each use is logged, never the sound.
"""
import base64
import logging
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from api import db, settings

logger = logging.getLogger(__name__)


class VaultError(Exception):
    """The recording cannot be read: it is missing, was changed, or its key is gone."""


class VaultNotConfigured(VaultError):
    """No master key is configured: nothing can be kept."""


class LocalKeyService:
    """Locks and unlocks recordings' keys with master keys read from VOICE_MASTER_KEYS.

    This is the seam for a real key service (an EU KMS or Vault): replace key_service() to return an adapter with
    the same three things, and nothing else in the vault changes."""

    def __init__(self, keys):
        self.keys = keys  # [(id, key)], newest first

    @classmethod
    def from_config(cls):
        keys = []
        for entry in filter(None, (part.strip() for part in settings.voice_master_keys().split(","))):
            key_id, _, encoded = entry.partition(":")
            try:
                key = base64.b64decode(encoded)
            except ValueError:
                key = b""
            if not key_id or len(key) != 32:
                raise VaultNotConfigured("VOICE_MASTER_KEYS must be `id:base64 32-byte key` entries.")
            keys.append((key_id, key))
        if not keys:
            raise VaultNotConfigured("No master key is set: recordings cannot be kept.")
        return cls(keys)

    @property
    def newest_id(self):
        return self.keys[0][0].encode()

    @staticmethod
    def locked_with(wrapped):
        """The id of the master key that locked `wrapped`."""
        return bytes(wrapped).partition(b".")[0]

    def wrap(self, data_key, context):
        key_id, master = self.keys[0]
        nonce = os.urandom(12)
        return key_id.encode() + b"." + nonce + AESGCM(master).encrypt(nonce, data_key, context)

    def unwrap(self, wrapped, context):
        key_id, _, rest = bytes(wrapped).partition(b".")
        master = dict(self.keys).get(key_id.decode(errors="replace"))
        if master is None:
            raise VaultError("The key that locked this recording is no longer held.")
        try:
            return AESGCM(master).decrypt(rest[:12], rest[12:], context)
        except InvalidTag as exc:
            raise VaultError("The recording's key does not open.") from exc


def key_service():
    """The key service that locks the recordings' keys."""
    return LocalKeyService.from_config()


def _bound_to(clone_id):
    return f"voice-demo-recording:{clone_id}".encode()


def store(clone_id, audio, filename="recording.wav"):
    """Keep `audio` (bytes) for the clone, encrypted; replaces what was kept."""
    keys = key_service()
    data_key = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(12)
    ciphertext = AESGCM(data_key).encrypt(nonce, audio, _bound_to(clone_id))
    db.run("INSERT INTO voice_recordings (clone_id, ciphertext, nonce, wrapped_key, filename) VALUES (?, ?, ?, ?, ?) "
           "ON CONFLICT (clone_id) DO UPDATE SET ciphertext = excluded.ciphertext, nonce = excluded.nonce, "
           "wrapped_key = excluded.wrapped_key, filename = excluded.filename",
           (clone_id, ciphertext, nonce, keys.wrap(data_key, _bound_to(clone_id)), filename))
    logger.info("Voice recording %s stored (%d bytes encrypted)", clone_id, len(ciphertext))


def load(clone_id):
    """The clone's recording, decrypted."""
    recording = db.one("SELECT * FROM voice_recordings WHERE clone_id = ?", (clone_id,))
    if recording is None:
        raise VaultError("There is no recording for this voice.")
    data_key = key_service().unwrap(recording["wrapped_key"], _bound_to(clone_id))
    try:
        audio = AESGCM(data_key).decrypt(bytes(recording["nonce"]), bytes(recording["ciphertext"]), _bound_to(clone_id))
    except InvalidTag as exc:
        raise VaultError("The recording was changed and cannot be read.") from exc
    logger.info("Voice recording %s read", clone_id)
    return audio


def filename_of(clone_id):
    row = db.one("SELECT filename FROM voice_recordings WHERE clone_id = ?", (clone_id,))
    return row["filename"] if row else None


def destroy(clone_id):
    """Delete the clone's recording and its locked key."""
    db.run("DELETE FROM voice_recordings WHERE clone_id = ?", (clone_id,))
    logger.info("Voice recording %s destroyed", clone_id)


def rewrap_all():
    """Lock every recording's key with the newest master key; returns how many were locked by an older one."""
    keys = key_service()
    done = 0
    for recording in db.many("SELECT clone_id, wrapped_key FROM voice_recordings"):
        if keys.locked_with(recording["wrapped_key"]) == keys.newest_id:
            continue
        context = _bound_to(recording["clone_id"])
        data_key = keys.unwrap(recording["wrapped_key"], context)
        db.run("UPDATE voice_recordings SET wrapped_key = ? WHERE clone_id = ?",
               (keys.wrap(data_key, context), recording["clone_id"]))
        done += 1
    logger.info("Voice recordings locked with the newest master key: %d", done)
    return done
