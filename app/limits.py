"""User-configurable size limits (Settings → Limits): the maximum size of a
single file entering the system, and the total storage quota for the file
browser's storage/ directory.

Enforced on transfers, uploads and remote preview fetches. Raises LimitError
with a translated message; callers map it to HTTP 400/413 or a failed task.
"""
import os

from app import db
from app.i18n import t

from config import STORAGE_DIR


class LimitError(Exception):
    pass


def _int_setting(key, default):
    try:
        return int(db.get_setting(key, default))
    except (TypeError, ValueError):
        return default


def max_single_mb():
    return _int_setting("max_single_file_mb", 512)


def max_storage_mb():
    return _int_setting("max_storage_mb", 2048)


def check_single(size, label="File"):
    """Raise if `size` bytes exceeds the single-file limit."""
    if size and size > max_single_mb() * 1024 * 1024:
        raise LimitError(t("err_file_too_large", name=label,
                           mb=max_single_mb()))


def storage_used():
    """Total bytes on disk under storage/ (staged files + in-flight relays)."""
    total = 0
    try:
        names = os.listdir(STORAGE_DIR)
    except OSError:
        return 0
    for name in names:
        try:
            path = os.path.join(STORAGE_DIR, name)
            if os.path.isfile(path):
                total += os.path.getsize(path)
        except OSError:
            pass
    return total


def check_quota(incoming=0):
    """Raise if storage/ usage + `incoming` bytes exceeds the quota."""
    if storage_used() + incoming > max_storage_mb() * 1024 * 1024:
        raise LimitError(t("err_storage_full", mb=max_storage_mb()))
