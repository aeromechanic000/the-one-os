"""Verify local-host detection (app/locality.py).

Part A — fresh DB: hosts added via the API get their is_local flag set in
the background (127.0.0.1, this machine's LAN IP and hostname -> Local;
an external documentation IP -> not Local).

Part B — legacy DB: a database created with the pre-is_local schema is
migrated in place (ALTER TABLE) and the pre-seeded row is marked at boot.
"""
import re
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time

import requests

PORT = 11695
BASE = f"http://127.0.0.1:{PORT}"


def wait_for(pred, timeout=15, what="condition"):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.2)
    raise AssertionError("timeout waiting for " + what)


def start_app(db_path):
    log = tempfile.NamedTemporaryFile(mode="w+", suffix=".log", delete=False)
    proc = subprocess.Popen(
        [sys.executable, "main.py", "--port", str(PORT)],
        stdout=log, stderr=subprocess.STDOUT, text=True,
        start_new_session=True, env={**__import__("os").environ,
                                     "WEBOS_DB": db_path})

    def fail(msg):
        proc.terminate()
        log.seek(0)
        raise SystemExit(f"FAIL: {msg}\n--- server log ---\n{log.read()}")

    password, deadline = None, time.time() + 20
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
        fail("server did not start")
    while time.time() < deadline:
        try:
            if requests.get(f"{BASE}/auth/login", timeout=1).ok:
                return proc, password
        except requests.RequestException:
            time.sleep(0.5)
    fail("login page never came up")


def legacy_db(path):
    """Build a DB with the OLD hosts schema (no is_local column)."""
    conn = sqlite3.connect(path)
    conn.executescript("""
    CREATE TABLE hosts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        host TEXT NOT NULL,
        port INTEGER NOT NULL DEFAULT 22,
        username TEXT NOT NULL DEFAULT 'root',
        auth_method TEXT NOT NULL DEFAULT 'password',
        key_path TEXT DEFAULT '',
        password_encrypted TEXT DEFAULT '',
        group_name TEXT DEFAULT '',
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    INSERT INTO hosts (name, host) VALUES ('legacy-self', '127.0.0.1');
    """)
    conn.commit()
    conn.close()


def flags(s):
    return {h["name"]: h["local"] for h in s.get(f"{BASE}/api/hosts").json()}


def add_host(s, name, addr):
    r = s.post(f"{BASE}/api/hosts", json={
        "name": name, "host": addr, "port": 22, "username": "root",
        "auth_method": "password", "password": "x"})
    assert r.status_code == 201, r.text


def part_a():
    db_dir = tempfile.mkdtemp(prefix="locality-a-")
    proc, password = start_app(db_dir + "/a.db")
    try:
        s = requests.Session()
        assert s.post(f"{BASE}/auth/login",
                      json={"password": password}).ok
        lan = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        lan.connect(("8.8.8.8", 80))
        lan_ip = lan.getsockname()[0]
        lan.close()

        add_host(s, "lo", "127.0.0.1")
        add_host(s, "lan", lan_ip)
        add_host(s, "byname", socket.gethostname())
        add_host(s, "far", "192.0.2.10")  # TEST-NET-1, never local

        wait_for(lambda: flags(s).get("lo") is True, what="loopback flag")
        wait_for(lambda: flags(s).get("lan") is True, what="LAN flag")
        wait_for(lambda: flags(s).get("byname") is True, what="hostname flag")
        wait_for(lambda: flags(s).get("far") is False, what="external flag")
        print("[A] add-time marking: loopback/LAN/hostname -> Local, "
              "external -> not Local")
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def part_b():
    db_dir = tempfile.mkdtemp(prefix="locality-b-")
    db = db_dir + "/b.db"
    legacy_db(db)
    proc, password = start_app(db)
    try:
        s = requests.Session()
        assert s.post(f"{BASE}/auth/login",
                      json={"password": password}).ok
        # migration ran at boot; pre-seeded row marked by recheck_all
        wait_for(lambda: flags(s).get("legacy-self") is True,
                 what="boot-time marking of migrated row")
        cols = sqlite3.connect(db).execute(
            "PRAGMA table_info(hosts)").fetchall()
        assert any(c[1] == "is_local" for c in cols), cols
        print("[B] legacy schema migrated + row marked Local at boot")
    finally:
        proc.terminate()
        proc.wait(timeout=5)


if __name__ == "__main__":
    part_a()
    part_b()
    print("ALL LOCALITY CHECKS PASSED")
