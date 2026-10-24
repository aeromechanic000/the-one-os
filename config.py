"""Static configuration and per-boot runtime secrets.

All secrets live in memory only: a fresh password, SECRET_KEY and BOOT_ID are
generated at every import (i.e. every process start), so restarting the service
invalidates cookies, sessions and encrypted host passwords (IDEA.md §1, §6.2).
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
SECRET_KEY = secrets.token_urlsafe(32)
BOOT_ID = secrets.token_urlsafe(16)
# Host-password encryption key: random per boot, so encrypted host passwords
# from a previous run cannot be decrypted after restart (re-entry required).
FERNET = Fernet(Fernet.generate_key())
