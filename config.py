"""Static configuration and runtime secrets.

The login password is memory-only and regenerated at every import (i.e. every
process start). Session secrets (SECRET_KEY, BOOT_ID) persist across restarts
so a signed-in browser stays signed in up to SESSION_MAX_AGE; encrypted host
passwords keep the per-boot key and require re-entry after a restart
(IDEA.md §1, §6.2).
"""
import os
import secrets

from cryptography.fernet import Fernet

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STORAGE_DIR = os.path.join(BASE_DIR, "storage")
CACHE_DIR = os.path.join(BASE_DIR, "cache")
PREVIEW_CACHE_DIR = os.path.join(CACHE_DIR, "preview")
REMOTE_CACHE_DIR = os.path.join(CACHE_DIR, "remote")
DB_PATH = os.environ.get("WEBOS_DB", os.path.join(BASE_DIR, "webos.db"))

# --- sessions ---------------------------------------------------------------
SESSION_MAX_AGE = 7 * 24 * 3600  # 7 days (IDEA.md §6.2)

# --- background threads -----------------------------------------------------
MONITOR_INTERVAL = 30          # seconds between TCP probes
MONITOR_TIMEOUT = 3            # seconds per probe
CLEANUP_INTERVAL = 3600        # hourly cleanup cycle
TRANSFER_TTL = 24 * 3600       # staged transfer files expiry
PREVIEW_CACHE_TTL = 24 * 3600  # preview cache expiry
REMOTE_CACHE_TTL = 24 * 3600

# --- terminal ----------------------------------------------------------------
TERMINAL_RETENTION = 600       # keep dead sessions 10 minutes for reattach
TERMINAL_BUFFER = 64 * 1024    # replay buffer per session
READ_CHUNK = 4096

# --- preview ------------------------------------------------------------------
PREVIEW_TEXT_LIMIT = 2 * 1024 * 1024   # 2 MB text preview
REMOTE_CACHE_LIMIT = 10 * 1024 * 1024  # small remote files cached on disk
LIBREOFFICE_BIN = os.environ.get("WEBOS_SOFFICE", "soffice")
LIBREOFFICE_TIMEOUT = 30

# --- attention rules ----------------------------------------------------------
MAX_RULES = 200
ATTENTION_LINE_LIMIT = 2048    # rule input truncated to last N chars
REGEX_CACHE_SIZE = 256
SAMPLE_TEST_TIMEOUT = 3        # seconds

# --- per-boot secrets (memory only) -------------------------------------------
PASSWORD = secrets.token_urlsafe(12)
# Host-password encryption key: random per boot, so encrypted host passwords
# from a previous run cannot be decrypted after restart (re-entry required).
FERNET = Fernet(Fernet.generate_key())

# --- session secrets (persisted, user's own installation) ---------------------
# SECRET_KEY and BOOT_ID used to be re-generated per boot, which silently
# logged every browser out on any restart or worker respawn. Like the
# AI-token key below they persist next to the DB so sessions survive
# restarts, still capped at SESSION_MAX_AGE by app/auth.py.
def _persistent_token(filename, nbytes):
    path = os.path.join(os.path.dirname(DB_PATH) or ".", filename)
    try:
        with open(path, encoding="ascii") as fh:
            value = fh.read().strip()
        if value:
            return value
    except (OSError, UnicodeDecodeError):
        pass
    value = secrets.token_urlsafe(nbytes)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as fh:
        fh.write(value + "\n")
    return value


SECRET_KEY = _persistent_token("session.key", 32)
BOOT_ID = _persistent_token("boot_id.key", 16)

# --- AI provider tokens (persisted, user's own third-party credentials) --------
# Unlike host passwords, stored LLM API tokens must survive a restart or the
# usage monitor would need re-entry every boot — so they use a dedicated
# key file (0600, generated once, kept next to the DB) instead of the
# per-boot key.
def _init_ai_token_fernet():
    key_path = os.path.join(os.path.dirname(DB_PATH) or ".", "ai_tokens.key")
    try:
        with open(key_path, "rb") as fh:
            return Fernet(fh.read().strip())
    except OSError:
        pass
    raw = Fernet.generate_key()
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(raw)
    return Fernet(raw)


AI_TOKEN_FERNET = _init_ai_token_fernet()
