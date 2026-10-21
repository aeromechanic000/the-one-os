"""e2e_files_test.py — SFTP-backed file browser / preview / transfer tests.

Runs an in-process paramiko SSH+SFTP server over a temp directory, registers
it as a Web OS host, and verifies:

  1. GET /api/files/remote (listing + entries)
  2. GET /api/preview remote text + image + markdown
  3. GET /api/files/download remote
  4. transfer remote->local (mode A) completes and lands in storage
  5. upload local->remote lands on the SFTP FS
  6. mkdir / rename / delete remote ops
  7. attention + terminal are untouched (no regression check here)

Usage: uv run python scripts/e2e_files_test.py <base_url> <password>
"""
import json
import os
import shutil
import socket
import stat as statmod
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import http.cookiejar

import paramiko
import socketio
from paramiko import SFTPAttributes, SFTPHandle, SFTPServer, \
    SFTPServerInterface
from paramiko.sftp import SFTP_FAILURE, SFTP_NO_SUCH_FILE, SFTP_OK

BASE = (sys.argv[1:2] or ["http://127.0.0.1:1"])[0]
PASSWORD = (sys.argv[2:3] or [""])[0]
SSH_PORT = 2223


# ---------------- in-memory-usage tempdir SFTP ----------------
class StubSFTPHandle(SFTPHandle):
    def stat(self):
        try:
            return SFTPAttributes.from_stat(os.fstat(self.readfile.fileno()))
        except OSError as e:
            return SFTP_NO_SUCH_FILE


class StubSFTPServer(SFTPServerInterface):
    ROOT = None  # tempdir, set at startup

    def _real(self, path):
        return os.path.realpath(os.path.join(self.ROOT, path.lstrip("/")))

    def list_folder(self, path):
        path = self._real(path)
        try:
            out = []
            for fname in os.listdir(path):
                attr = SFTPAttributes.from_stat(os.stat(os.path.join(path, fname)))
                attr.filename = fname
                out.append(attr)
            return out
        except OSError:
            return SFTP_NO_SUCH_FILE

    def stat(self, path):
        try:
            return SFTPAttributes.from_stat(os.stat(self._real(path)))
        except OSError:
            return SFTP_NO_SUCH_FILE

    def lstat(self, path):
        try:
            return SFTPAttributes.from_stat(os.lstat(self._real(path)))
        except OSError:
            return SFTP_NO_SUCH_FILE

    def open(self, path, flags, attr):
        path = self._real(path)
        try:
            binary_flag = getattr(os, "O_BINARY", 0)
            flags |= binary_flag
            mode = getattr(attr, "st_mode", None) or 0o666
            fd = os.open(path, flags, mode)
        except OSError as e:
            return SFTP_NO_SUCH_FILE
        if flags & os.O_WRONLY:
            fstr = "ab" if flags & os.O_APPEND else "wb"
        elif flags & os.O_RDWR:
            fstr = "ab+" if flags & os.O_APPEND else "rb+"
        else:
            fstr = "rb"
        try:
            f = os.fdopen(fd, fstr)
        except OSError:
            return SFTP_FAILURE
        fobj = StubSFTPHandle(flags)
        fobj.filename = path
        fobj.readfile = f
        fobj.writefile = f
        return fobj

    def remove(self, path):
        try:
            os.remove(self._real(path))
            return SFTP_OK
        except OSError:
            return SFTP_FAILURE

    def rename(self, oldpath, newpath):
        try:
            os.rename(self._real(oldpath), self._real(newpath))
            return SFTP_OK
        except OSError:
            return SFTP_FAILURE

    def mkdir(self, path, attr):
        try:
            os.mkdir(self._real(path), 0o755)
            return SFTP_OK
        except OSError:
            return SFTP_FAILURE

    def rmdir(self, path):
        try:
            os.rmdir(self._real(path))
            return SFTP_OK
        except OSError:
            return SFTP_FAILURE


class Server(paramiko.ServerInterface):
    def __init__(self):
        self.pending = {}   # chanid -> 'shell' | 'sftp'

    def get_allowed_auths(self, username):
        return "publickey"

    def check_auth_publickey(self, username, key):
        return (paramiko.AUTH_SUCCESSFUL
                if username == "test" and
                key.get_fingerprint() == FINGERPRINT
                else paramiko.AUTH_FAILED)

    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_SUCCEEDED if kind == "session" \
            else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_shell_request(self, chanid):
        self.pending[chanid] = "shell"
        return True

    def check_channel_subsystem_request(self, channel, name):
        if name == "sftp":
            self.pending[channel.chanid] = "sftp"
            return True
        return False


FINGERPRINT = None  # set in main()


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

            def handle(client=client):
                try:
                    transport = paramiko.Transport(client)
                    transport.add_server_key(host_key)
                    server = Server()
                    transport.start_server(server=server)
                    # one connection may carry several sequential channels
                    # (the app pools its SSH client per host)
                    while True:
                        chan = transport.accept(30)
                        if chan is None:
                            return
                        kind = None
                        for _ in range(60):
                            kind = server.pending.get(chan.chanid)
                            if kind:
                                break
                            time.sleep(0.05)
                        if kind == "sftp":
                            sftp_srv = SFTPServer(chan, "sftp", server,
                                                  sftp_si=StubSFTPServer)
                            # blocks until this sftp session ends
                            sftp_srv.start_subsystem("sftp", transport, chan)
                        elif kind == "shell":
                            chan.send(b"test@fakehost:~$ ")
                            while chan.recv(4096):
                                pass
                            return
                except Exception:
                    pass
            threading.Thread(target=handle, daemon=True).start()

    threading.Thread(target=accept_loop, daemon=True).start()
    return sock


# ---------------- webos helpers ----------------
def make_api(cookie):
    def api(method, path, payload=None, raw=False):
        req = urllib.request.Request(
            BASE + path,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={"Content-Type": "application/json", "Cookie": cookie},
            method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read()
                if raw:
                    return resp.status, body, dict(resp.headers)
                return resp.status, json.loads(body) if body else {}
        except urllib.error.HTTPError as e:
            body = e.read()
            try:
                return e.code, json.loads(body) if body else {}
            except ValueError:
                return e.code, body
    return api


def wait_for(predicate, timeout=15, what="condition"):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.15)
    raise AssertionError("timeout waiting for " + what)


def main():
    global FINGERPRINT
    root = tempfile.mkdtemp(prefix="webos-sftp-")
    StubSFTPServer.ROOT = root
    with open(os.path.join(root, "hello.txt"), "w") as f:
        f.write("hello from fake sftp\n")
    with open(os.path.join(root, "note.md"), "w") as f:
        f.write("# Title\n\n*emphasized* text with <script>alert(1)</script>\n")
    tiny_png = bytes.fromhex(
        "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
        "1f15c4890000000d4944415478da6364f8cfc000000301010018dd8db0"
        "0000000049454e44ae426082")
    with open(os.path.join(root, "pic.png"), "wb") as f:
        f.write(tiny_png)

    host_key = paramiko.RSAKey.generate(2048)
    client_key = paramiko.RSAKey.generate(2048)
    FINGERPRINT = client_key.get_fingerprint()
    key_path = os.path.join(root, "..", "e2e_id_rsa")
    key_path = os.path.abspath(key_path)
    client_key.write_private_key_file(key_path)
    os.chmod(key_path, 0o600)
    ssh_server(host_key)

    # login
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar))
    req = urllib.request.Request(
        BASE + "/auth/login",
        data=('{"password":"%s"}' % PASSWORD).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    opener.open(req)
    cookie = "; ".join("%s=%s" % (c.name, c.value) for c in jar)
    api = make_api(cookie)

    _, hosts = api("GET", "/api/hosts")
    for h in (hosts or []):
        if h["name"] == "e2e-sftp":
            api("DELETE", "/api/hosts/" + str(h["id"]))
    status, created = api("POST", "/api/hosts", {
        "name": "e2e-sftp", "host": "127.0.0.1", "port": SSH_PORT,
        "username": "test", "auth_method": "key", "key_path": key_path})
    assert status == 201, created
    host_id = created["id"]

    # sio only to receive transfer progress broadcasts
    sio = socketio.Client()
    progress = {}
    sio.on("transfer_progress", lambda d: progress.__setitem__(d["id"], d))
    sio.connect(BASE, headers={"Cookie": cookie},
                transports=["websocket", "polling"])

    try:
        # 1. listing
        status, data = api("GET", "/api/files/remote?host_id=%d" % host_id)
        assert status == 200, data
        names = [e["name"] for e in data["entries"]]
        assert "hello.txt" in names and "note.md" in names and "pic.png" in names, names
        print("[files-e2e] remote listing OK:", sorted(names))

        def rpath(name):
            return "/".join([data["path"].rstrip("/"), name])

        # 2. previews
        status, pv = api("GET", "/api/preview?source=remote&host_id=%d&path=%s"
                         % (host_id, urllib.parse.quote(rpath("hello.txt"))))
        assert status == 200 and pv["type"] == "text" and \
            "hello from fake sftp" in pv["content"], pv
        print("[files-e2e] remote text preview OK")

        status, pv = api("GET", "/api/preview?source=remote&host_id=%d&path=%s"
                         % (host_id, urllib.parse.quote(rpath("note.md"))))
        assert status == 200 and pv["type"] == "markdown", pv
        assert "<script>" in pv["content"]  # source passes through; DOMPurify is frontend
        print("[files-e2e] remote markdown preview OK (sanitized client-side)")

        status, body, headers = api(
            "GET", "/api/preview?source=remote&host_id=%d&path=%s"
            % (host_id, urllib.parse.quote(rpath("pic.png"))), raw=True)
        assert status == 200 and headers.get("Content-Type", "").startswith("image/"), headers
        assert body == tiny_png
        print("[files-e2e] remote image preview OK (%d bytes)" % len(body))

        # 3. download
        status, body, headers = api(
            "GET", "/api/files/download?target=remote&host_id=%d&path=%s"
            % (host_id, urllib.parse.quote(rpath("hello.txt"))), raw=True)
        assert status == 200 and b"hello from fake sftp" in body, (status, headers)
        print("[files-e2e] remote download OK")

        # 4. transfer remote -> local (mode A)
        status, task = api("POST", "/api/transfer/run", {
            "src": {"kind": "remote", "host_id": host_id,
                    "path": rpath("hello.txt")},
            "dst": {"kind": "local", "path": "from-remote-hello.txt"},
            "mode": "A"})
        assert status == 202, task
        tid = task["id"]
        wait_for(lambda: progress.get(tid, {}).get("state") in
                 ("completed", "failed"), what="transfer completion")
        assert progress[tid]["state"] == "completed", progress[tid]
        status, local = api("GET", "/api/files/local?path=")
        assert any(e["name"] == "from-remote-hello.txt" for e in local["entries"]), local
        status, pv = api("GET", "/api/preview?source=local&path=from-remote-hello.txt")
        assert "hello from fake sftp" in pv["content"]
        print("[files-e2e] transfer remote->local (mode A) OK + local preview OK")

        # 5. upload local -> remote
        boundary = "----webosboundary"
        file_body = ("--%s\r\nContent-Disposition: form-data; name=\"target\""
                     "\r\n\r\nremote\r\n"
                     "--%s\r\nContent-Disposition: form-data; name=\"host_id\""
                     "\r\n\r\n%d\r\n"
                     "--%s\r\nContent-Disposition: form-data; name=\"path\""
                     "\r\n\r\n/\r\n"
                     "--%s\r\nContent-Disposition: form-data; name=\"file\"; "
                     "filename=\"uploaded.txt\"\r\nContent-Type: text/plain"
                     "\r\n\r\nuploaded via webos\r\n--%s--\r\n"
                     % (boundary, boundary, host_id, boundary, boundary,
                        boundary)).encode()
        req = urllib.request.Request(
            BASE + "/api/files/upload", data=file_body,
            headers={"Content-Type": "multipart/form-data; boundary=" + boundary,
                     "Cookie": cookie}, method="POST")
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
        assert os.path.isfile(os.path.join(root, "uploaded.txt"))
        with open(os.path.join(root, "uploaded.txt")) as f:
            assert f.read() == "uploaded via webos"
        print("[files-e2e] upload local->remote OK")

        # 6. ops: mkdir, rename, delete
        status, _ = api("POST", "/api/files/op", {
            "op": "mkdir", "target": "remote", "host_id": host_id,
            "path": "/", "new_path": "newdir"})
        assert status == 200, _
        assert statmod.S_ISDIR(os.stat(os.path.join(root, "newdir")).st_mode)
        status, _ = api("POST", "/api/files/op", {
            "op": "rename", "target": "remote", "host_id": host_id,
            "path": "/newdir", "new_path": "/renamedir"})
        assert status == 200 and os.path.isdir(os.path.join(root, "renamedir"))
        status, _ = api("POST", "/api/files/op", {
            "op": "delete", "target": "remote", "host_id": host_id,
            "path": "/renameddir"})
        status, _ = api("POST", "/api/files/op", {
            "op": "delete", "target": "remote", "host_id": host_id,
            "path": "/renamedir"})
        assert status == 200 and not os.path.exists(os.path.join(root, "renamedir"))
        print("[files-e2e] remote mkdir/rename/delete OK")

        # 7. path traversal is rejected
        status, _ = api("GET", "/api/files/local?path=" +
                        urllib.parse.quote("../webos.db"))
        assert status == 400, "local traversal not blocked: %s" % status
        status, _ = api("GET", "/api/preview?source=remote&host_id=%d&path=%s"
                        % (host_id, urllib.parse.quote("../../etc/passwd")))
        assert status == 400, "remote traversal not blocked: %s" % status
        print("[files-e2e] path traversal blocked (local + remote)")
    finally:
        api("DELETE", "/api/hosts/" + str(host_id))
        sio.disconnect()
        shutil.rmtree(root, ignore_errors=True)
    print("[files-e2e] ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
