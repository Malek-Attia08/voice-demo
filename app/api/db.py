"""The demo's small database (SQLite): the cloned voices and their encrypted recordings.

Two tables (VoiceClone, VoiceRecording), for one clinic. One active clone per language.
"""
import os
import sqlite3

from api import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS voice_clones (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    language TEXT NOT NULL,
    mistral_voice_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    active INTEGER NOT NULL DEFAULT 0,
    retention_days INTEGER NOT NULL DEFAULT 365,
    speaker_name TEXT NOT NULL,
    consented_at TEXT NOT NULL,
    consent_wording TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'imported',
    reading_check TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS voice_clones_one_active_per_language ON voice_clones (language) WHERE active = 1;
CREATE TABLE IF NOT EXISTS voice_recordings (
    clone_id TEXT PRIMARY KEY REFERENCES voice_clones (id) ON DELETE CASCADE,
    ciphertext BLOB NOT NULL,
    nonce BLOB NOT NULL,
    wrapped_key BLOB NOT NULL,
    filename TEXT NOT NULL DEFAULT 'recording.wav',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now'))
);
"""


def connect():
    path = settings.db_path()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def one(sql, args=()):
    conn = connect()
    try:
        return conn.execute(sql, args).fetchone()
    finally:
        conn.close()


def many(sql, args=()):
    conn = connect()
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def run(sql, args=()):
    """Run a write; returns the number of rows it changed."""
    conn = connect()
    try:
        with conn:
            return conn.execute(sql, args).rowcount
    finally:
        conn.close()
