"""e2e_terminal_test.py — full-stack terminal test.

Starts an in-process paramiko SSH server (key auth + fake shell that echoes
and can emit a sudo password prompt), registers it as a Web OS host via the
REST API, drives a terminal through SocketIO, and verifies:

  1. terminal_create -> terminal_output (prompt arrives)
  2. input echo round-trip
  3. sudo prompt -> session_attention (waiting-input rule engine fires)
  4. answering -> session_attention_cleared

Usage: uv run python scripts/e2e_terminal_test.py <base_url> <password>
"""
import os
import socket
import sys
import tempfile
import threading
import time
import urllib.request
import http.cookiejar

import paramiko
import socketio

BASE = sys.argv[1]
PASSWORD = sys.argv[2]
SSH_PORT = 2222

PROMPT = b"test@fakehost:~$ "
SUDO_PROMPT = b"[sudo] password for test: "


class FakeShellServer(paramiko.ServerInterface):
    def __init__(self):
        self.shell_event = threading.Event()

    def get_allowed_auths(self, username):
        return "publickey"

    def check_auth_publickey(self, username, key):
        return paramiko.AUTH_SUCCESSFUL if username == "test" \
            else paramiko.AUTH_FAILED

    def check_channel_request(self, kind, chanid):
        if kind == "session":
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_pty_request(self, *a, **kw):
        return True

    def check_channel_shell_request(self, chanid):
        self.shell_event.set()
        return True


def serve_client(chan):
    chan.send(PROMPT)
    buf = b""
    while True:
        try:
            data = chan.recv(4096)
        except Exception:
            return
        if not data:
            return
        buf += data
        if buf.rstrip(b"\r\n").endswith(b"sudo apt update") or \
                b"sudo" in buf and buf.endswith(b"\r") or buf.endswith(b"\n"):
            cmd = buf.strip()
            buf = b""
            if cmd.startswith(b"sudo"):
                chan.send(SUDO_PROMPT)
                # wait for the password answer
                while True:
                    try:
                        ans = chan.recv(4096)
                    except Exception:
                        return
                    if not ans:
                        return
                    if ans.endswith(b"\r") or ans.endswith(b"\n"):
                        chan.send(b"\r\nOK\r\n")
                        chan.send(PROMPT)
                        break
            else:
                chan.send(b"ECHO: " + cmd + b"\r\n")
                chan.send(PROMPT)
        elif data.endswith(b"\r"):
            cmd = buf.strip()
            buf = b""
            chan.send(b"\r\nECHO: " + cmd + b"\r\n")
            chan.send(PROMPT)


def ssh_server(host_key):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", SSH_PORT))
    sock.listen(4)

    def accept_loop():
        while True:
            try:
                client, _ = sock.accept()
            except OSError:
                return
            # each connection in its own guard: the Web OS monitor probes
            # the port with a bare TCP connect and closes without a banner
            def handle(client=client):
                try:
                    transport = paramiko.Transport(client)
                    transport.add_server_key(host_key)
                    server = FakeShellServer()
                    transport.start_server(server=server)
                    chan = transport.accept(20)
                    if chan is None:
                        return
                    server.shell_event.wait(5)
                    serve_client(chan)
                except Exception:
                    pass
            threading.Thread(target=handle, daemon=True).start()

    threading.Thread(target=accept_loop, daemon=True).start()
    return sock


class Collector:
    def __init__(self, sio):
        self.output = {}      # sid -> bytes
        self.attention = {}   # sid -> event
        self.cleared = set()
        self.meta = {}
        sio.on("terminal_output", self.on_output)
        sio.on("session_attention", self.on_attention)
        sio.on("session_attention_cleared", self.on_cleared)
        sio.on("terminal_meta", self.on_meta)

    def on_output(self, data):
        self.output[data["sid"]] = \
            self.output.get(data["sid"], b"") + data["data"].encode("utf-8",
                                                                    "replace")

    def on_attention(self, data):
        self.attention[data["sid"]] = data

    def on_cleared(self, data):
        self.cleared.add(data["sid"])

    def on_meta(self, data):
        self.meta[data["sid"]] = data


def api(method, path, payload=None, cookie=None):
    req = urllib.request.Request(
        BASE + path,
        data=payload and __import__("json").dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 **({"Cookie": cookie} if cookie else {})},
        method=method)
    with urllib.request.urlopen(req) as resp:
        body = resp.read()
        return resp.status, __import__("json").loads(body) if body else {}


def wait_for(predicate, timeout=12, what="condition"):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    raise AssertionError("timeout waiting for " + what)


def main():
    # 1. throwaway SSH host key + client key
    host_key = paramiko.RSAKey.generate(2048)
    client_key = paramiko.RSAKey.generate(2048)
    known = tempfile.mkdtemp(prefix="webos-e2e-")
    client_path = os.path.join(known, "id_rsa")
    client_key.write_private_key_file(client_path)
    os.chmod(client_path, 0o600)
    ssh_server(host_key)

    # authorize exactly this key by fingerprint
    orig_check = FakeShellServer.check_auth_publickey
    fingerprint = client_key.get_fingerprint()

    def check_auth(self, username, key):
        return paramiko.AUTH_SUCCESSFUL \
            if username == "test" and key.get_fingerprint() == fingerprint \
            else paramiko.AUTH_FAILED
    FakeShellServer.check_auth_publickey = check_auth

    # 2. login to Web OS
    status, _ = api("POST", "/auth/login",
                    {"password": PASSWORD})
    assert status == 200
    # (cookie from urllib above was in a default opener; redo with jar)
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar))
    req = urllib.request.Request(
        BASE + "/auth/login",
        data=('{"password":"%s"}' % PASSWORD).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    opener.open(req)
    cookie = "; ".join("%s=%s" % (c.name, c.value) for c in jar)

    # idempotency: remove leftovers from a previous crashed run
    status, hosts = api("GET", "/api/hosts", None, cookie)
    for h in (hosts or []):
        if h["name"] == "e2e-fake":
            api("DELETE", "/api/hosts/" + str(h["id"]), None, cookie)

    status, created = api("POST", "/api/hosts", {
        "name": "e2e-fake", "host": "127.0.0.1", "port": SSH_PORT,
        "username": "test", "auth_method": "key", "key_path": client_path,
    }, cookie)
    assert status == 201, created
    host_id = created["id"]

    # 3. socketio session
    sio = socketio.Client()
    col = Collector(sio)
    sio.connect(BASE, headers={"Cookie": cookie},
                transports=["websocket", "polling"])
    assert sio.connected

    ack = sio.call("terminal_create", {"host_id": host_id}, timeout=15)
    assert ack and ack.get("ok"), f"terminal_create failed: {ack}"
    sid = ack["sid"]
    print("[e2e] terminal created:", sid)

    wait_for(lambda: col.output.get(sid, b"").endswith(PROMPT),
             what="initial prompt")
    print("[e2e] prompt received via terminal_output")

    sio.emit("terminal_input", {"sid": sid, "data": "echo hello\r"})
    wait_for(lambda: b"ECHO: echo hello" in col.output.get(sid, b""),
             what="echo round-trip")
    print("[e2e] input echo round-trip OK")

    # 4. sudo prompt -> attention (high confidence builtin rule)
    col.output[sid] = b""
    sio.emit("terminal_input", {"sid": sid, "data": "sudo apt update\r"})
    wait_for(lambda: SUDO_PROMPT in col.output.get(sid, b""),
             what="sudo prompt")
    wait_for(lambda: sid in col.attention, timeout=8,
             what="session_attention")
    att = col.attention[sid]
    assert att["name"] == "sudo password", att
    assert att["confidence"] == "high", att
    print("[e2e] waiting-input detection fired:", att["name"],
          "hint=", repr(att["hint"]))

    # 5. answer -> cleared
    sio.emit("terminal_input", {"sid": sid, "data": "hunter2\r"})
    wait_for(lambda: sid in col.cleared, timeout=8,
             what="session_attention_cleared")
    print("[e2e] attention cleared after answering")

    # 6. list + close
    status, terms = api("GET", "/api/terminals", None, cookie)
    assert any(t["sid"] == sid for t in terms), terms
    sio.emit("terminal_close", {"sid": sid})
    time.sleep(0.5)

    api("DELETE", "/api/hosts/" + str(host_id), None, cookie)
    sio.disconnect()
    print("[e2e] ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
