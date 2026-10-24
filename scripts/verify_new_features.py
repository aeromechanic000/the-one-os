"""Ad-hoc verification of the new settings/AI/history endpoints.

Starts the server, logs in, and exercises: settings round-trip (limits + AI
config), the AI chat proxy error path (unreachable base URL), op-history
recording, and desktop page rendering of the theme/chat assets.
"""
import re
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

PORT = 11697
BASE = f"http://127.0.0.1:{PORT}"
MOCK_PORT = 11698


class _MockAI(BaseHTTPRequestHandler):
    """Minimal OpenAI-compatible /chat/completions endpoint."""

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        body = ('{"choices": [{"message": {"content": "hello from mock"}}],'
                ' "usage": {"total_tokens": 1}}').encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def start_mock_ai():
    srv = ThreadingHTTPServer(("127.0.0.1", MOCK_PORT), _MockAI)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main():
    log = tempfile.NamedTemporaryFile(mode="w+", suffix=".log", delete=False)
    proc = subprocess.Popen(
        [sys.executable, "main.py", "--port", str(PORT)],
        stdout=log, stderr=subprocess.STDOUT, text=True,
        start_new_session=True,  # own group so finally can kill it all
        env={**__import__("os").environ,
             "WEBOS_DB": tempfile.mkdtemp(prefix="verify-db-") + "/verify.db"})
    try:
        run_checks(proc, log)
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def run_checks(proc, log):

    def fail(msg):
        proc.terminate()
        log.seek(0)
        raise SystemExit(f"FAIL: {msg}\n--- server log ---\n{log.read()}")

    # wait for the banner; fail fast if the port is already taken (an
    # orphaned instance would answer requests with a different password)
    password = None
    deadline = time.time() + 20
    while time.time() < deadline:
        log.seek(0)
        text = log.read()
        if "Address already in use" in text:
            fail(f"port {PORT} is taken — kill the old instance first")
        m = re.search(r"Password\s+:\s*(\S+)", text)
        if m:
            password = m.group(1)
            break
        time.sleep(0.5)
    if not password:
        fail("server did not come up / no password in banner")
    # ...and wait until it actually accepts connections
    while time.time() < deadline:
        try:
            if requests.get(f"{BASE}/auth/login", timeout=1).ok:
                break
        except requests.RequestException:
            pass
        time.sleep(0.5)

    s = requests.Session()
    r = s.post(f"{BASE}/auth/login", json={"password": password})
    assert r.ok, r.text

    # settings: defaults present, ciphertext not leaked
    r = s.get(f"{BASE}/api/settings").json()
    assert r["max_single_file_mb"] == "512" and r["max_storage_mb"] == "2048"
    assert r["history_days"] == "90" and r["ai_api_key_set"] is False
    assert "ai_api_key_enc" not in r

    # settings: round-trip new keys + api key handling
    r = s.put(f"{BASE}/api/settings", json={
        "max_single_file_mb": 10, "history_days": 30,
        "ai_base_url": f"{BASE}/v1", "ai_model": "test-model",
        "ai_api_key": "sk-secret"}).json()
    assert r["max_single_file_mb"] == "10" and r["history_days"] == "30"
    assert r["ai_api_key_set"] is True and "ai_api_key_enc" not in r

    # invalid values rejected silently
    r = s.put(f"{BASE}/api/settings", json={"ai_base_url": "ftp://bad"}).json()
    assert r["ai_base_url"] == f"{BASE}/v1", "invalid URL should be ignored"

    # AI chat proxy: unreachable base URL -> clean error, not a crash
    s.put(f"{BASE}/api/settings", json={"ai_base_url": "http://127.0.0.1:9/v1"})
    r = s.post(f"{BASE}/api/ai/chat",
               json={"messages": [{"role": "user", "content": "hi"}]},
               timeout=150)
    assert r.status_code in (400, 502), r.text
    assert r.json().get("error"), "expected an error surface"

    # AI chat proxy: happy path against a mock OpenAI-compatible server
    mock = start_mock_ai()
    s.put(f"{BASE}/api/settings", json={
        "ai_base_url": f"http://127.0.0.1:{MOCK_PORT}/v1",
        "ai_model": "mock-model"})
    r = s.post(f"{BASE}/api/ai/chat",
               json={"messages": [{"role": "user", "content": "summarize"}]},
               timeout=30)
    mock.shutdown()
    assert r.ok, r.text
    assert r.json().get("reply") == "hello from mock", r.text

    # history recorded: login + settings_update + ai_chat
    r = s.get(f"{BASE}/api/history?days=7").json()
    kinds = {row["type"] for row in r}
    assert {"settings_update", "ai_chat", "login"} <= kinds, kinds

    # not-configured path
    s.put(f"{BASE}/api/settings",
          json={"ai_base_url": "", "ai_model": "", "ai_api_key": ""})
    r = s.post(f"{BASE}/api/ai/chat",
               json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 400, r.text

    # desktop page: theme + chat assets present
    html = s.get(f"{BASE}/").text
    assert "theme.js" in html and "chat.js" in html and "ai-panel" in html

    print("ALL NEW-FEATURE CHECKS PASSED")


if __name__ == "__main__":
    main()
