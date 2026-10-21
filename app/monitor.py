"""Background online monitoring (IDEA.md §6.5).

Probes SSH port reachability of every host every 30 seconds via a raw TCP
connect (no SSH auth), keeps results in memory, and pushes host_status on
change. Host CRUD triggers an immediate re-probe via hosts_changed.
"""
import socket
import threading

from app import db
from config import MONITOR_INTERVAL, MONITOR_TIMEOUT

_state = {}          # host_id -> True/False/None (unknown)
_lock = threading.Lock()
_wake = threading.Event()


def get_status():
    with _lock:
        return dict(_state)


def all_status():
    """Status map plus counts for the top bar."""
    with _lock:
        st = dict(_state)
    total = len(st)
    online = sum(1 for v in st.values() if v is True)
    return {"hosts": st, "total": total, "online": online}


def probe_host(host):
    try:
        with socket.create_connection(
                (host["host"], int(host["port"] or 22)),
                timeout=MONITOR_TIMEOUT):
            return True
    except OSError:
        return False


def probe_all(emit_changes=True):
    rows = db.query("SELECT id, host, port FROM hosts")
    for row in rows:
        online = probe_host(row)
        with _lock:
            prev = _state.get(row["id"])
            _state[row["id"]] = online
            changed = prev != online
        if emit_changes and (changed or prev is None):
            from app.sockets import emit
            emit("host_status", {"host_id": row["id"], "online": online})
        _wake.wait(0.05)  # stagger probes slightly


def drop_host(host_id):
    with _lock:
        _state.pop(host_id, None)


def hosts_changed():
    """Called by hosts CRUD: new probe + push so clients refresh lists."""
    from app.sockets import emit
    emit("hosts_changed", {})
    threading.Thread(target=probe_all, kwargs={"emit_changes": True},
                     daemon=True).start()


def _loop():
    while True:
        try:
            probe_all()
        except Exception:
            pass
        _wake.wait(MONITOR_INTERVAL)
        _wake.clear()


def start():
    t = threading.Thread(target=_loop, daemon=True, name="monitor")
    t.start()
