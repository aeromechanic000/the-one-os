"""Hourly cleanup: expired transfer files and preview cache (IDEA.md §2).

- storage/ files whose staged_files rows expired (>24h) are deleted.
- cache/preview/ and cache/remote/ entries older than 24h are deleted.
"""
import os
import threading
import time

from app import db
from config import (CLEANUP_INTERVAL, PREVIEW_CACHE_DIR, PREVIEW_CACHE_TTL,
                    REMOTE_CACHE_DIR, REMOTE_CACHE_TTL, STORAGE_DIR,
                    TRANSFER_TTL)


def _cleanup_staged(now):
    cutoff = time.strftime(
        "%Y-%m-%d %H:%M:%S", time.gmtime(now - TRANSFER_TTL))
    rows = db.query(
        "SELECT id, stored_path FROM staged_files WHERE expires_at < ?",
        (cutoff,))
    for row in rows:
        try:
            if row["stored_path"] and row["stored_path"].startswith(STORAGE_DIR):
                if os.path.isfile(row["stored_path"]):
                    os.remove(row["stored_path"])
        except OSError:
            pass
        db.execute("DELETE FROM staged_files WHERE id = ?", (row["id"],))
    # Also sweep orphans: files on disk with no DB row older than TTL.
    try:
        names = os.listdir(STORAGE_DIR)
    except OSError:
        names = []
    for name in names:
        path = os.path.join(STORAGE_DIR, name)
        try:
            if os.path.isfile(path) and (now - os.path.getmtime(path)) > TRANSFER_TTL:
                known = db.query_one(
                    "SELECT id FROM staged_files WHERE stored_path = ?", (path,))
                if not known:
                    os.remove(path)
        except OSError:
            pass


def _cleanup_dir(directory, ttl):
    try:
        names = os.listdir(directory)
    except OSError:
        return
    now = time.time()
    for name in names:
        path = os.path.join(directory, name)
        try:
            if os.path.isfile(path) and (now - os.path.getmtime(path)) > ttl:
                os.remove(path)
        except OSError:
            pass


def run_once():
    now = time.time()
    _cleanup_staged(now)
    _cleanup_dir(PREVIEW_CACHE_DIR, PREVIEW_CACHE_TTL)
    _cleanup_dir(REMOTE_CACHE_DIR, REMOTE_CACHE_TTL)


def _loop():
    while True:
        time.sleep(CLEANUP_INTERVAL)
        try:
            run_once()
            from app.ssh_manager import manager
            manager.reap_expired()  # piggyback terminal retention reaping
        except Exception:
            pass


def start():
    threading.Thread(target=_loop, daemon=True, name="cleanup").start()
