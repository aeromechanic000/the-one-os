"""socket_smoke.py — verify the SocketIO layer: auth-gated connect,
ack round-trip, and graceful terminal_create failure.

Usage: uv run python scripts/socket_smoke.py <base_url> <password>
"""
import sys
import time
import urllib.request
import http.cookiejar

import socketio

BASE = sys.argv[1]
PASSWORD = sys.argv[2]


def login_cookie():
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    req = urllib.request.Request(
        BASE + "/auth/login",
        data=('{"password": %s}' % ('"' + PASSWORD + '"')).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with opener.open(req) as resp:
        assert resp.status == 200, "login failed"
    return "; ".join(f"{c.name}={c.value}" for c in jar)


def main():
    cookie = login_cookie()
    sio = socketio.Client(http_session=None, request_timeout=10)
    got_ack = {}

    @sio.on("terminal_meta")
    def _meta(data):
        got_ack["meta"] = data

    sio.connect(BASE, headers={"Cookie": cookie},
                transports=["websocket", "polling"])
    assert sio.connected, "socket.io connect failed (auth?)"
    print("[socket-smoke] connected over", sio.transport())

    t0 = time.time()
    ack = sio.call("latency_probe", {}, timeout=5)
    assert ack and "t" in ack, f"latency ack broken: {ack}"
    print(f"[socket-smoke] latency_probe round-trip OK "
          f"({(time.time() - t0) * 1000:.0f} ms)")

    ack = sio.call("terminal_create", {"host_id": 999999}, timeout=15)
    assert ack and ack.get("ok") is False, f"expected failure ack, got {ack}"
    print(f"[socket-smoke] terminal_create error path OK: {ack.get('error')}")

    ack = sio.call("terminal_attach", {"sid": "nope"}, timeout=5)
    assert ack and ack.get("ok") is False
    print("[socket-smoke] terminal_attach missing session OK")

    sio.disconnect()
    print("[socket-smoke] ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
