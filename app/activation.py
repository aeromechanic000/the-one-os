"""Single-active-frontend tracking (IDEA.md §1: one user, many devices).

The OS has one user but may be opened from several machines. Exactly one
logged-in frontend is "active" at a time — the most recent login or wake
wins; every other frontend falls asleep and may only read state until it
takes over again. Like the password, this state is memory-only and resets
on restart (first frontend to load after a boot becomes active).
"""
import threading
import time

_lock = threading.Lock()
_active = {}   # {"client_id", "address", "activated_at"}


def register(client_id, address):
    """Make this client the active frontend and tell every socket."""
    global _active
    with _lock:
        _active = {
            "client_id": client_id,
            "address": address,
            "activated_at": time.time(),
        }
        snapshot = dict(_active)
    _broadcast(snapshot)
    return snapshot


def clear(client_id):
    """Forget the active entry when that client logs out (others wake up
    manually — no auto-promotion, takeover is always explicit)."""
    global _active
    with _lock:
        if _active.get("client_id") == client_id:
            _active = {}


def current():
    with _lock:
        return dict(_active) if _active else None


def is_active(client_id):
    if not client_id:
        return False
    with _lock:
        return _active.get("client_id") == client_id


def state_for(client_id, your_ip=""):
    """The payload a frontend needs: its identity plus who is active."""
    cur = current()
    return {
        "client_id": client_id,
        "active": bool(cur and cur.get("client_id") == client_id),
        "your_ip": your_ip,
        "active_address": (cur or {}).get("address", ""),
        "activated_at": (cur or {}).get("activated_at", 0.0),
    }


def _broadcast(active):
    from app import sockets   # lazy: avoid an import cycle at module load
    sockets.emit("activation_changed", {
        "client_id": active["client_id"],
        "address": active["address"],
        "activated_at": active["activated_at"],
    })
