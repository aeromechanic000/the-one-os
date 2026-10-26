"""Validate: adding the local machine's loopback / LAN / public address as
a host — does it behave exactly like any other host?

Starts a throwaway SSH server (password auth, echo shell, exec responder,
read-only SFTP rooted in a temp dir) on 0.0.0.0:<port>, a One OS instance
with an isolated DB, then checks, per address:

  loopback (127.0.0.1)  — monitor online, terminal create/input/output via
                           SocketIO, quick-action exec, SFTP listing
  LAN (this machine)    — the same full set over the LAN interface
  public (if resolvable)— TCP probe result, monitor status, and the clean
                           failure path if the router does not hairpin

Usage: uv run python scripts/validate_local_host.py
"""
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time

import paramiko
import requests
import socketio

PORT = 11693
BASE = f"http://127.0.0.1:{PORT}"
SSH_PORT = 2223
SSH_USER, SSH_PASS = "osuser", "ospass"
PROMPT = b"test@fakehost:~$ "
SFTP_ROOT = None  # set in main

results = []      # (label, ok, note)


def check(label, ok, note=""):
    results.append((label, ok, note))
    print(("[PASS] " if ok else "[FAIL] ") + label +
          (" — " + note if note else ""))


class FakeHostServer(paramiko.ServerInterface):
    """Password-auth SSH server: echo shell, exec responder, mini SFTP."""

    def __init__(self):
        self.shell_event = threading.Event()

    def get_allowed_auths(self, username):
        return "password"

    def check_auth_password(self, username, password):
        ok = (username, password) == (SSH_USER, SSH_PASS)
        return paramiko.AUTH_SUCCESSFUL if ok else paramiko.AUTH_FAILED

    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_SUCCEEDED if kind == "session" \
            else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_pty_request(self, *a, **kw):
        return True

    def check_channel_shell_request(self, chanid):
        self.shell_event.set()  # interactive shell: drive the echo loop
        return True

    def check_channel_exec_request(self, channel, command):
        def run():
            try:
                channel.send(b"ran[%s]: %s\n"
                             % (os.uname().nodename.encode(), command))
                channel.send_exit_status(0)
            finally:
                channel.close()
        threading.Thread(target=run, daemon=True).start()
        return True

    def check_channel_subsystem_request(self, channel, name):
        if name == "sftp":
            # 4th arg = sftp_si: the SFTPServerInterface that receives
            # file requests (the default wrapper returns "unsupported")
            handler = MiniSFTPServer(channel, name, self, MiniSFTPInterface)
            handler.start()
            return True  # no echo loop: SFTP owns this channel's stream
        return False

class MiniSFTPInterface(paramiko.SFTPServerInterface):
    """Read-only SFTP view of SFTP_ROOT: listing + stat only (raw paths)."""

    def _under(self, path):
        return os.path.join(SFTP_ROOT, (path or "/").lstrip("/"))

    def _attrs(self, name, st):
        a = paramiko.SFTPAttributes()
        a.filename = name
        a.st_size = st.st_size
        a.st_mtime = int(st.st_mtime)
        a.st_mode = st.st_mode
        return a

    def list_folder(self, path):
        try:
            out = []
            for name in os.listdir(self._under(path)):
                st = os.lstat(os.path.join(self._under(path), name))
                out.append(self._attrs(name, st))
            return out
        except OSError:
            return paramiko.SFTP_NO_SUCH_FILE

    def stat(self, path):
        try:
            return self._attrs(os.path.basename(path),
                               os.stat(self._under(path)))
        except OSError:
            return paramiko.SFTP_NO_SUCH_FILE

    def lstat(self, path):
        try:
            return self._attrs(os.path.basename(path),
                               os.lstat(self._under(path)))
        except OSError:
            return paramiko.SFTP_NO_SUCH_FILE


class MiniSFTPServer(paramiko.SFTPServer):
    """SFTP protocol plumbing only; file ops live on FakeHostServer
    (that is where paramiko dispatches them). _realpath maps normalize()
    into the temp root."""

    def _realpath(self, path):
        return SFTP_ROOT + self.canonicalize(path or ".")


def ssh_listener(host_key):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", SSH_PORT))
    sock.listen(8)

    def accept_loop():
        while True:
            try:
                client, _ = sock.accept()
            except OSError:
                return

            def handle(client=client):
                try:
                    transport = paramiko.Transport(client)
                    transport.add_server_key(host_key)
                    server = FakeHostServer()
                    transport.start_server(server=server)
                    chan = transport.accept(20)
                    if chan is None:
                        return
                    # only drive the echo loop for interactive shells;
                    # exec replies itself and the SFTP handler owns its
                    # channel (a recv loop here would eat protocol bytes)
                    server.shell_event.wait(5)
                    if server.shell_event.is_set():
                        serve_shell(chan)
                    while transport.is_active():
                        time.sleep(0.2)
                except Exception:
                    pass
            threading.Thread(target=handle, daemon=True).start()

    threading.Thread(target=accept_loop, daemon=True).start()
    return sock


def serve_shell(chan):
    chan.send(PROMPT)
    buf = b""
    while True:
        try:
            data = chan.recv(4096)
        except Exception:
            return
        if not data:
            return
        buf += data  # a whole line may arrive in a single chunk
        if buf.endswith(b"\r") or buf.endswith(b"\n"):
            cmd = buf.strip()
            buf = b""
            chan.send(b"\r\nECHO: " + cmd + b"\r\n" + PROMPT)


def wait_for(pred, timeout=15, what="condition"):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.1)
    raise AssertionError("timeout waiting for " + what)


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))  # no packets sent; just picks a route
        return s.getsockname()[0]
    finally:
        s.close()


def public_ip():
    for url in ("https://api.ipify.org", "https://ifconfig.me/ip"):
        try:
            r = requests.get(url, timeout=6)
            if r.ok and re.fullmatch(r"[\d.]+", r.text.strip()):
                return r.text.strip()
        except requests.RequestException:
            continue
    return None


def main():
    global SFTP_ROOT
    SFTP_ROOT = tempfile.mkdtemp(prefix="fakehost-sftp-")
    with open(os.path.join(SFTP_ROOT, "hello.txt"), "w") as f:
        f.write("local host validation\n")
    db_dir = tempfile.mkdtemp(prefix="validate-hosts-")

    host_key = paramiko.RSAKey.generate(2048)
    ssh_listener(host_key)

    srv_log = tempfile.NamedTemporaryFile(mode="w+", suffix=".log",
                                          delete=False)
    srv = subprocess.Popen(
        [sys.executable, "main.py", "--port", str(PORT)],
        stdout=srv_log, stderr=subprocess.STDOUT, text=True,
        start_new_session=True,
        env={**os.environ, "WEBOS_DB": db_dir + "/validate.db"})
    try:
        run_checks(srv_log)
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=5)
        except subprocess.TimeoutExpired:
            srv.kill()


def run_checks(srv_log):
    # wait for the server banner
    password = None
    deadline = time.time() + 20
    while time.time() < deadline:
        srv_log.seek(0)
        m = re.search(r"Password\s+:\s*(\S+)", srv_log.read())
        if m:
            password = m.group(1)
            break
        time.sleep(0.5)
    assert password, "One OS did not start"
    while time.time() < deadline:
        try:
            if requests.get(f"{BASE}/auth/login", timeout=1).ok:
                break
        except requests.RequestException:
            time.sleep(0.5)

    s = requests.Session()
    r = s.post(f"{BASE}/auth/login", json={"password": password})
    assert r.ok

    jar = s.cookies
    cookie = "; ".join(f"{c.name}={c.value}" for c in jar)

    def api(method, path, payload=None):
        r = s.request(method, BASE + path, json=payload)
        return r.status_code, (r.json() if r.content else {})

    lan = lan_ip()
    pub = public_ip()
    print(f"loopback=127.0.0.1  lan={lan}  public={pub or 'unresolvable'}\n")

    # --- add hosts: loopback + LAN, password auth like a typical host -----
    hosts = {}
    for name, addr in [("loopback-self", "127.0.0.1"), ("lan-self", lan)]:
        st, created = api("POST", "/api/hosts", {
            "name": name, "host": addr, "port": SSH_PORT,
            "username": SSH_USER, "auth_method": "password",
            "password": SSH_PASS})
        assert st == 201, created
        hosts[name] = created["id"]

    def host_row(hid):
        return next(h for h in api("GET", "/api/hosts")[1]
                    if h["id"] == hid)

    for name in ("loopback-self", "lan-self"):
        wait_for(lambda: host_row(hosts[name])["online"] is True,
                 timeout=15, what=f"{name} online")
        check(f"monitor: {name} shows Online",
              True, "TCP probe on the address behaves like any host")

    # --- full terminal round-trip over SocketIO, for both addresses -------
    sio = socketio.Client()
    output = {}

    @sio.on("terminal_output")
    def _out(data):
        output[data["sid"]] = output.get(data["sid"], b"") + \
            data["data"].encode("utf-8", "replace")

    sio.connect(BASE, headers={"Cookie": cookie},
                transports=["websocket", "polling"])

    for name in ("loopback-self", "lan-self"):
        output.clear()
        ack = sio.call("terminal_create", {"host_id": hosts[name]},
                       timeout=20)
        assert ack and ack.get("ok"), f"terminal_create failed: {ack}"
        sid = ack["sid"]
        wait_for(lambda: output.get(sid, b"").endswith(PROMPT),
                 what=f"{name} initial prompt")
        sio.emit("terminal_input", {"sid": sid, "data": "echo hello\r"})
        wait_for(lambda: b"ECHO: echo hello" in output.get(sid, b""),
                 what=f"{name} echo round-trip")
        sio.emit("terminal_close", {"sid": sid})
        check(f"terminal: {name} full round-trip",
              True, "SSH shell + pty + input/output over this address")

    # --- quick action (exec channel) on the LAN address --------------------
    st, action = api("POST", "/api/actions", {
        "name": "validate-echo", "command_template": "echo hi"})
    assert st == 201, action
    st, run = api("POST",
                  f"/api/actions/{action['id']}/run",
                  {"host_id": hosts["lan-self"]})
    check("quick action exec over LAN address",
          st == 200 and "ran[" in run.get("output", ""),
          repr(run.get("output", ""))[:60])
    api("DELETE", f"/api/actions/{action['id']}")

    # --- SFTP listing (file browser remote view) on loopback ---------------
    st, listing = api("GET",
                      f"/api/files/remote?host_id={hosts['loopback-self']}&path=/")
    names = [e["name"] for e in listing.get("entries", [])]
    check("SFTP file listing (remote browser)",
          st == 200 and "hello.txt" in names, f"entries={names}")

    # --- public address ------------------------------------------------
    if pub:
        probe = socket.socket()
        probe.settimeout(4)
        try:
            probe.connect((pub, SSH_PORT))
            reachable = True
        except OSError:
            reachable = False
        finally:
            probe.close()
        st, created = api("POST", "/api/hosts", {
            "name": "pub-self", "host": pub, "port": SSH_PORT,
            "username": SSH_USER, "auth_method": "password",
            "password": SSH_PASS})
        pub_id = created["id"]
        wait_for(lambda: host_row(pub_id)["online"] is not None,
                 timeout=15, what="public probe result")
        online = host_row(pub_id)["online"]
        if reachable:
            wait_for(lambda: host_row(pub_id)["online"] is True,
                     timeout=5, what="public online")
            check("public address: reachable, shows Online", True,
                  "router hairpins the connection")
        else:
            check("public address: shows Offline when unreachable",
                  online is False,
                  "same as any remote host behind a firewall")
            ack = sio.call("terminal_create", {"host_id": pub_id},
                           timeout=15)
            check("public address: terminal fails cleanly",
                  not (ack and ack.get("ok")), f"ack={ack}")
        api("DELETE", f"/api/hosts/{pub_id}")
    else:
        print("[skip] public IP unresolvable (network) — behavior equals "
              "any unreachable host: monitor Offline, connect error")

    for hid in hosts.values():
        api("DELETE", f"/api/hosts/{hid}")
    sio.disconnect()

    print()
    failed = [r for r in results if not r[1]]
    print(f"{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
