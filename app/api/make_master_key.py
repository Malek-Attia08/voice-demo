"""Makes the vault's master key and writes it into .env as VOICE_MASTER_KEYS, never printing it.

    python -m api.make_master_key [path to .env]            first key (refuses if one exists)
    python -m api.make_master_key [path to .env] --rotate   a new key goes first, the old ones stay to open old recordings

After a rotation, run the care once (python -m api.care) to lock every recording with the new key; when no recording
is locked by an old key any more, the old entry can be removed from VOICE_MASTER_KEYS. Never share a key between
environments: each .env makes its own.
"""
import base64
import os
import secrets
import sys

VARIABLE = "VOICE_MASTER_KEYS"


class KeyExists(Exception):
    """A master key is already set; rotating is explicit."""


def _read(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as handle:
        return handle.read().splitlines()


def make_key(path, rotate=False):
    """Make a key and write it into the env file at `path`; returns the new key's id."""
    lines = _read(path)
    index = next((i for i, line in enumerate(lines) if line.startswith(f"{VARIABLE}=")), None)
    current = [entry for entry in (lines[index].partition("=")[2].split(",") if index is not None else []) if entry.strip()]
    if current and not rotate:
        raise KeyExists("A master key is already set.")
    key_id = f"k{secrets.token_hex(3)}"
    entry = f"{key_id}:{base64.b64encode(secrets.token_bytes(32)).decode()}"
    line = f"{VARIABLE}={','.join([entry, *current])}"
    if index is None:
        lines.append(line)
    else:
        lines[index] = line
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")
    return key_id


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    rotate = "--rotate" in args
    paths = [arg for arg in args if not arg.startswith("--")]
    path = paths[0] if paths else ".env"
    try:
        key_id = make_key(path, rotate=rotate)
    except KeyExists:
        print(f"{VARIABLE} is already set in {path}. Use --rotate to add a new key in front of it.")
        return 1
    print(f"{VARIABLE} now starts with key {key_id} in {path} (the key itself is not shown).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
