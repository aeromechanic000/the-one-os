"""SSH/SFTP connection management (IDEA.md §6.3).

- One Paramiko invoke_shell() channel per terminal session, stored in
  {sid: TerminalSession} with locking; reader threads push output via SocketIO.
- Dead sessions are retained TERMINAL_RETENTION seconds for reattach/replay.
- Pooled per-host SSHClient used for SFTP + exec (actions, transfers, browse).
"""
import codecs
import threading
import time
import uuid

import paramiko

from app import db
from app.attention import AttentionEngine
from config import (FERNET, READ_CHUNK, TERMINAL_BUFFER, TERMINAL_RETENTION)


class SSHError(Exception):
    pass


class NeedsPassword(SSHError):
    """Stored host password cannot be decrypted (service restarted)."""


def _emit(event, data):
    """Emit without importing sockets at module level (avoid a cycle)."""
    from app import sockets
    sockets.emit(event, data)


def get_host_or_fail(host_id):
    row = db.query_one("SELECT * FROM hosts WHERE id = ?", (host_id,))
    if not row:
        raise SSHError("host not found")
    return row


def _auth_kwargs(host):
    if host["auth_method"] == "key":
        if not host["key_path"]:
            raise SSHError("key file path is empty")
        kwargs = {"key_filename": host["key_path"],
                  "look_for_keys": False, "allow_agent": False}
        blob = host["password_encrypted"]
        if blob:
            try:
                kwargs["passphrase"] = FERNET.decrypt(blob.encode()).decode()
            except Exception:
                raise NeedsPassword("key passphrase re-entry needed")
        return kwargs
    blob = host["password_encrypted"]
    if not blob:
        raise SSHError("no password stored for this host")
    try:
        password = FERNET.decrypt(blob.encode()).decode()
    except Exception:
        raise NeedsPassword("password re-entry needed after restart")
    return {"password": password, "look_for_keys": False, "allow_agent": False}


class TerminalSession:
    def __init__(self, host, cols=80, rows=24):
        self.sid = uuid.uuid4().hex[:12]
        self.host_id = host["id"]
        self.host_label = host["name"]
        self.host_addr = host["host"]
        self.state = "connecting"   # connecting | connected | closed
        self.closed_at = None
        self.created_at = time.time()
        self.buffer = ""            # replay tail for reattach
        self.client = None
        self.channel = None
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._lock = threading.Lock()
        self.engine = AttentionEngine(
            self.sid, _emit,
            on_hit=lambda rule_id: _bump_hits(rule_id),
        )

    # -- lifecycle -----------------------------------------------------------
    def connect(self, cols, rows):
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=self.host_addr,
            port=int(self._host()["port"] or 22),
            username=self._host()["username"],
            timeout=10, banner_timeout=10, auth_timeout=10,
            **_auth_kwargs(self._host()),
        )
        self.client = client
        self.channel = client.invoke_shell(
            term="xterm-256color", width=cols, height=rows)
        self.state = "connected"
        reader = threading.Thread(target=self._read_loop, daemon=True)
        reader.start()

    def _host(self):
        return get_host_or_fail(self.host_id)

    def _read_loop(self):
        try:
            while True:
                data = self.channel.recv(READ_CHUNK)
                if not data:
                    break
                text = self._decoder.decode(data)
                with self._lock:
                    self.buffer = (self.buffer + text)[-TERMINAL_BUFFER:]
                _emit("terminal_output", {"sid": self.sid, "data": text})
                self.engine.on_output(text)
        except Exception:
            pass
        finally:
            self._finish()

    def _finish(self):
        already = self.state == "closed"
        self.state = "closed"
        self.closed_at = time.time()
        self.engine.on_close()
        if not already:
            _emit("terminal_closed", {"sid": self.sid, "reason": "closed"})
        try:
            if self.channel:
                self.channel.close()
        except Exception:
            pass
        try:
            if self.client:
                self.client.close()
        except Exception:
            pass

    # -- operations ------------------------------------------------------------
    def send(self, text):
        with self._lock:
            if self.state != "connected" or not self.channel:
                raise SSHError("session is not connected")
            self.channel.send(text)
        self.engine.on_input(text)

    def resize(self, cols, rows):
        with self._lock:
            if self.channel and self.state == "connected":
                try:
                    self.channel.resize_pty(cols, rows)
                except Exception:
                    pass

    def close(self):
        self._finish()

    def expired(self):
        return (
            self.state == "closed"
            and self.closed_at
            and (time.time() - self.closed_at) > TERMINAL_RETENTION
        )

    def meta(self):
        att = self.engine.active
        return {
            "sid": self.sid,
            "host_id": self.host_id,
            "name": self.host_label,
            "state": self.state,
            "attention": bool(att),
            "attention_hint": att and {
                "name": att["name"],
                "confidence": att["confidence"],
                "hint": None,
            },
        }


def _bump_hits(rule_id):
    from app.attention_rules import bump_hits
    bump_hits(rule_id)


class SSHManager:
    def __init__(self):
        self.terminals = {}
        self._clients = {}   # host_id -> SSHClient
        self._lock = threading.RLock()

    # -- terminals -----------------------------------------------------------
    def create_terminal(self, host_id, cols=80, rows=24):
        host = get_host_or_fail(host_id)
        session = TerminalSession(host, cols, rows)
        session.connect(cols, rows)   # raises on failure -> no session stored
        with self._lock:
            self.terminals[session.sid] = session
        _emit("terminal_meta", session.meta())
        return session

    def get(self, sid):
        with self._lock:
            return self.terminals.get(sid)

    def send(self, sid, text):
        s = self.get(sid)
        if not s:
            raise SSHError("no such session")
        s.send(text)

    def resize(self, sid, cols, rows):
        s = self.get(sid)
        if s:
            s.resize(cols, rows)

    def close(self, sid):
        s = self.get(sid)
        if s:
            with self._lock:
                self.terminals.pop(sid, None)
            s.close()

    def list_terminals(self):
        with self._lock:
            sessions = list(self.terminals.values())
        return [s.meta() for s in sessions]

    def reap_expired(self):
        with self._lock:
            dead = [sid for sid, s in self.terminals.items() if s.expired()]
            for sid in dead:
                self.terminals.pop(sid, None)

    def drop_host(self, host_id):
        with self._lock:
            sids = [sid for sid, s in self.terminals.items()
                    if s.host_id == host_id]
            sessions = [self.terminals.pop(sid) for sid in sids]
            client = self._clients.pop(host_id, None)
        for s in sessions:
            try:
                s.close()
            except Exception:
                pass
        if client:
            try:
                client.close()
            except Exception:
                pass
        return sids

    # -- pooled clients for SFTP / exec ----------------------------------------
    def get_client(self, host_id, fresh=False):
        with self._lock:
            if not fresh:
                client = self._clients.get(host_id)
                if client and client.get_transport() and \
                        client.get_transport().is_active():
                    return client
            old = self._clients.pop(host_id, None)
        if old:
            try:
                old.close()
            except Exception:
                pass
        host = get_host_or_fail(host_id)
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=host["host"], port=int(host["port"] or 22),
            username=host["username"],
            timeout=10, banner_timeout=10, auth_timeout=10,
            **_auth_kwargs(host),
        )
        with self._lock:
            self._clients[host_id] = client
        return client

    def with_sftp(self, host_id, fn):
        """Run fn(sftp); retry once on a stale pooled connection."""
        last_exc = None
        for attempt in (0, 1):
            try:
                client = self.get_client(host_id, fresh=attempt == 1)
                sftp = client.open_sftp()
                try:
                    return fn(sftp)
                finally:
                    try:
                        sftp.close()
                    except Exception:
                        pass
            except NeedsPassword:
                raise
            except (paramiko.SSHException, EOFError, OSError) as exc:
                last_exc = exc
                with self._lock:
                    self._clients.pop(host_id, None)
        raise SSHError(str(last_exc))

    def run_exec(self, host_id, command, timeout=30):
        """Execute one command on the host; returns (output, exit_status)."""
        client = self.get_client(host_id)
        transport = client.get_transport()
        chan = transport.open_session(timeout=timeout)
        chan.settimeout(timeout)
        chan.set_combine_stderr(True)
        chan.exec_command(command)
        chunks = []
        try:
            while True:
                data = chan.recv(READ_CHUNK)
                if not data:
                    break
                chunks.append(data.decode("utf-8", "replace"))
        except Exception:
            pass
        exit_status = chan.recv_exit_status()
        chan.close()
        return "".join(chunks), exit_status

    def shutdown(self):
        with self._lock:
            sessions = list(self.terminals.values())
            clients = list(self._clients.values())
            self.terminals.clear()
            self._clients.clear()
        for s in sessions:
            try:
                s.close()
            except Exception:
                pass
        for c in clients:
            try:
                c.close()
            except Exception:
                pass


manager = SSHManager()
