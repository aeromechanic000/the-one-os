"""SocketIO wiring: terminal streams and status pushes (IDEA.md §2, §6.3).

async_mode='threading' + simple-websocket: works on modern Python where
eventlet/gevent cannot go. Every emit is a broadcast — the system has a
single user, and mirroring all terminals to every open tab/phone is a
feature, not a leak.
"""
from flask import session
from flask_socketio import ConnectionRefusedError, SocketIO

from app import auth
from app import ssh_manager

socketio = None


def emit(event, data):
    """Thread-safe emit; safe to call before init / from any thread."""
    if socketio is not None:
        try:
            socketio.emit(event, data)
        except Exception:
            pass


def init_socketio(app):
    global socketio
    socketio = SocketIO(app, async_mode="threading", cors_allowed_origins="*")

    @socketio.on("connect")
    def on_connect():
        if not auth.session_valid():
            raise ConnectionRefusedError("unauthorized")

    @socketio.on("terminal_create")
    def on_terminal_create(data):
        host_id = int(data.get("host_id", 0))
        cols = int(data.get("cols", 80) or 80)
        rows = int(data.get("rows", 24) or 24)
        try:
            s = ssh_manager.manager.create_terminal(host_id, cols, rows)
        except ssh_manager.NeedsPassword as exc:
            return {"ok": False, "error": "needs_password",
                    "message": str(exc)}
        except Exception as exc:
            return {"ok": False, "error": "connect_failed",
                    "message": str(exc)}
        return {"ok": True, "sid": s.sid, "name": s.host_label}

    @socketio.on("terminal_input")
    def on_terminal_input(data):
        sid = data.get("sid", "")
        text = data.get("data", "")
        if not sid or not isinstance(text, str):
            return
        try:
            ssh_manager.manager.send(sid, text)
        except ssh_manager.SSHError:
            pass

    @socketio.on("terminal_resize")
    def on_terminal_resize(data):
        try:
            ssh_manager.manager.resize(
                data.get("sid", ""),
                int(data.get("cols", 80) or 80),
                int(data.get("rows", 24) or 24),
            )
        except (TypeError, ValueError):
            pass

    @socketio.on("terminal_close")
    def on_terminal_close(data):
        ssh_manager.manager.close(data.get("sid", ""))

    @socketio.on("terminal_attach")
    def on_terminal_attach(data):
        """Replay recent buffer to a (re)connecting tab (10-min retention)."""
        s = ssh_manager.manager.get(data.get("sid", ""))
        if not s:
            return {"ok": False, "error": "no such session"}
        from flask_socketio import emit as _emit_to
        _emit_to("terminal_replay", {"sid": s.sid, "data": s.buffer})
        _emit_to("terminal_meta", s.meta())
        if s.engine.active:
            from app.attention import derive_hint
            _emit_to("session_attention", {
                "sid": s.sid,
                "name": s.engine.active["name"],
                "confidence": s.engine.active["confidence"],
                "hint": derive_hint(s.engine.state),
            })
        return {"ok": True}

    @socketio.on("latency_probe")
    def on_latency_probe(_data=None):
        import time
        return {"t": time.time()}

    return socketio
