"""Local-host detection: is a host entry actually this machine?

A host is "local" when its address matches a loopback, one of this
machine's interface addresses, its hostname, or — best effort — its
public IP (fetched in the background with a short timeout; may stay
unknown on isolated or flaky networks).

Checks run in background threads only: DNS resolution and the public-IP
lookup can block for seconds, so host add/update handlers just spawn
mark_host_async() and the UI refreshes via the hosts_changed push.
"""
import ipaddress
import socket
import threading
import time
import urllib.request

from app import db

PUBLIC_IP_TTL = 600        # seconds a fetched public IP stays trusted
PUBLIC_IP_FAIL_TTL = 120   # retry soon-ish after a failed lookup
LOCAL_TTL = 60             # seconds interface addresses stay cached

_cache_lock = threading.Lock()
_local = {"ips": set(), "hostnames": set(), "ts": 0.0}
_public = {"ip": None, "ts": 0.0}

_PUBLIC_SOURCES = ("https://api.ipify.org", "https://ifconfig.me/ip")


def _norm(ip):
    """Canonical string for an IP (drops IPv6 zone, compresses)."""
    try:
        return str(ipaddress.ip_address(str(ip).split("%", 1)[0]))
    except ValueError:
        return str(ip).lower()


def _collect_local():
    ips = {"127.0.0.1", "::1"}
    try:  # every address the hostname resolves to (all interfaces)
        for info in socket.getaddrinfo(socket.gethostname(), None):
            ips.add(info[4][0])
    except OSError:
        pass
    try:  # primary outbound address; no packet is actually sent
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ips.add(s.getsockname()[0])
        finally:
            s.close()
    except OSError:
        pass
    hostnames = {socket.gethostname().lower()}
    hostnames.add(socket.gethostname().split(".", 1)[0].lower())
    return {_norm(ip) for ip in ips}, hostnames


def _refresh_local(force=False):
    now = time.time()
    with _cache_lock:
        fresh = (now - _local["ts"]) < LOCAL_TTL
        if fresh and not force:
            return
        ips, hostnames = _collect_local()
        _local.update(ips=ips, hostnames=hostnames, ts=now)


def _fetch_public():
    for url in _PUBLIC_SOURCES:
        try:
            with urllib.request.urlopen(url, timeout=4) as resp:
                text = resp.read(64).decode("utf-8", "replace").strip()
            return str(ipaddress.ip_address(text))  # validate
        except Exception:
            continue
    return None


def public_ip(refresh=False):
    """Cached public address of this machine's NAT (or None if unknown)."""
    now = time.time()
    with _cache_lock:
        if _public["ip"] is not None:
            if not refresh and (now - _public["ts"]) < PUBLIC_IP_TTL:
                return _public["ip"]
        elif not refresh and (now - _public["ts"]) < PUBLIC_IP_FAIL_TTL:
            return None  # failed recently — do not hammer the service
    ip = _fetch_public()
    with _cache_lock:
        _public.update(ip=ip, ts=now)
    return ip


def is_local(host):
    """True when `host` (IP or hostname) points at this machine."""
    host = (host or "").strip().lower().strip("[]")
    if not host:
        return False
    _refresh_local()
    with _cache_lock:
        ips, hostnames = set(_local["ips"]), set(_local["hostnames"])
        pub = _public["ip"]
    if host in hostnames:
        return True

    def _hit(addr):
        try:
            if ipaddress.ip_address(addr).is_loopback:
                return True
        except ValueError:
            pass
        return addr in ips or (pub is not None and addr == pub)

    try:  # IP literal: no DNS involved
        return _hit(_norm(host))
    except ValueError:
        pass
    try:  # hostname: resolve and compare (may block — bg threads only)
        for info in socket.getaddrinfo(host, None):
            if _hit(_norm(info[4][0])):
                return True
    except OSError:
        pass
    return False


# --- host-row marking --------------------------------------------------------

def check_host(host_id):
    """Recompute is_local for one host row; returns the new value or None."""
    row = db.query_one("SELECT id, host, is_local FROM hosts WHERE id = ?",
                       (host_id,))
    if not row:
        return None
    val = 1 if is_local(row["host"]) else 0
    if val != row["is_local"]:
        db.execute("UPDATE hosts SET is_local = ? WHERE id = ?", (val, host_id))
    return val


def _emit_if_changed(host_id, before):
    row = db.query_one("SELECT is_local FROM hosts WHERE id = ?", (host_id,))
    if row and row["is_local"] != before:
        from app.sockets import emit
        emit("hosts_changed", {})


def mark_host_async(host_id):
    """Background-check one host (used on add/update); never blocks."""
    def run():
        try:
            row = db.query_one("SELECT is_local FROM hosts WHERE id = ?",
                               (host_id,))
            before = row["is_local"] if row else 0
            public_ip()  # best-effort; TTL + negative-TTL guarded
            check_host(host_id)
            _emit_if_changed(host_id, before)
        except Exception:
            pass
    threading.Thread(target=run, daemon=True,
                     name="locality-%s" % host_id).start()


def recheck_all_async():
    """Boot hook: two passes — interface matches land immediately, the
    second pass catches hosts on the public IP once it is known."""
    def run():
        try:
            _refresh_local(force=True)
            _recheck_all_once()
            public_ip()
            _recheck_all_once()
        except Exception:
            pass
    threading.Thread(target=run, daemon=True, name="locality-boot").start()


def _recheck_all_once():
    rows = db.query("SELECT id, is_local FROM hosts")
    changed = False
    for row in rows:
        new = check_host(row["id"])
        if new is not None and new != row["is_local"]:
            changed = True
    if changed:
        from app.sockets import emit
        emit("hosts_changed", {})


def maintain():
    """Hourly (cleanup.py): refresh the public IP; recheck on change."""
    before = public_ip()
    if before is None:
        return
    if public_ip(refresh=True) != before:
        _recheck_all_once()
