"""Network fetch for outbound probes (AI usage checks) that survives a
broken system resolver.

Why: on boxes where Tailscale MagicDNS manages /etc/resolv.conf without
usable upstream nameservers, every public getaddrinfo fails with
"Name or service not known". The usage monitor would be dead there even
though the machine has working IP connectivity.

How: when the normal fetch fails with a name-resolution error, resolve
the host ourselves — a minimal A-record query sent directly to public
resolvers over UDP (default 223.5.5.5 / 119.29.29.29, override with the
ONE_OS_DNS env var, comma-separated) — then retry once against that IP
with the hostname preserved for TLS SNI and certificate validation.
Results are cached for a few minutes.

Only stdlib; usage endpoints are simple GETs (no redirects expected).
"""
import http.client
import os
import random
import socket
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

MAX_BODY = 262144      # bytes read per response (same cap as before)
DNS_TIMEOUT = 2.0      # seconds per resolver
CACHE_TTL = 300        # seconds a resolved IP is reused

_CACHE = {}            # host -> (ip, expires_at)
_LOCK = threading.Lock()


def _resolvers():
    env = os.environ.get("ONE_OS_DNS", "")
    if env.strip():
        return [s.strip() for s in env.split(",") if s.strip()]
    return ["223.5.5.5", "119.29.29.29"]     # AliDNS / DNSPod, globally reachable


# --- minimal DNS client (A records only) ------------------------------------------

def _skip_name(data, off):
    """Skip a (possibly compressed) domain name; returns next offset."""
    while True:
        n = data[off]
        if n == 0:
            return off + 1
        if n & 0xC0 == 0xC0:                 # compression pointer
            return off + 2
        off += 1 + n


def _dns_query(host, server):
    """Ask `server` for the first A record of `host`. Returns (ip, ttl);
    raises RuntimeError with a short reason."""
    qid = random.getrandbits(16)
    qname = b"".join(bytes([len(p)]) + p.encode("ascii", "ignore")
                     for p in host.split(".")) + b"\x00"
    packet = (struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0)
              + qname + struct.pack(">HH", 1, 1))          # A / IN
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(DNS_TIMEOUT)
    try:
        sock.sendto(packet, (server, 53))
        data, _ = sock.recvfrom(4096)
    except OSError as exc:
        raise RuntimeError(str(exc)[:120])
    finally:
        sock.close()

    if len(data) < 12:
        raise RuntimeError("short DNS response")
    rid, flags, qd, an = struct.unpack(">HHHH", data[:8])
    if rid != qid:
        raise RuntimeError("DNS id mismatch")
    rcode = flags & 0x0F
    if rcode != 0:
        raise RuntimeError("DNS rcode %d" % rcode)         # 3 = NXDOMAIN
    off = 12
    try:
        for _ in range(qd):                                # skip question
            off = _skip_name(data, off) + 4
        for _ in range(an):
            off = _skip_name(data, off)
            rtype, _cls, ttl, rdlen = struct.unpack(">HHIH",
                                                    data[off:off + 10])
            off += 10
            if rtype == 1 and rdlen == 4:                  # A record
                ip = ".".join(str(b) for b in data[off:off + 4])
                return ip, ttl
            off += rdlen                                   # CNAME etc.
    except (IndexError, struct.error):
        raise RuntimeError("malformed DNS response")
    raise RuntimeError("no A record in answer")


def resolve(host):
    """Resolve `host` via direct DNS (bypasses the system resolver).
    Raises RuntimeError when every resolver fails."""
    now = time.time()
    with _LOCK:
        hit = _CACHE.get(host)
        if hit and hit[1] > now:
            return hit[0]
    errors = []
    for server in _resolvers():
        try:
            ip, ttl = _dns_query(host, server)
            with _LOCK:
                _CACHE[host] = (ip, now + min(ttl, CACHE_TTL))
            return ip
        except RuntimeError as exc:
            errors.append("%s: %s" % (server, exc))
    raise RuntimeError("direct DNS failed [%s] (%s)"
                       % (host, "; ".join(errors)[:160]))


def _is_dns_error(exc):
    """True when a fetch failed at the name-resolution step."""
    reason = getattr(exc, "reason", exc)
    if isinstance(reason, socket.gaierror):
        return True
    text = str(reason).lower()
    return ("name or service not known" in text
            or "getaddrinfo" in text
            or "name does not resolve" in text
            or "temporary failure in name resolution" in text)


# --- fetch -------------------------------------------------------------------------

def _pinned_get(url, headers, ip, timeout):
    """GET `url` connecting to `ip` but presenting the URL's hostname for
    TLS, so SNI and certificate checks stay correct. Returns (status, body)."""
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    if parts.scheme == "http":
        conn = http.client.HTTPConnection(host, parts.port or 80,
                                          timeout=timeout)
        plain = True
    else:
        conn = http.client.HTTPSConnection(host, parts.port or 443,
                                           timeout=timeout)
        plain = False
    try:
        sock = socket.create_connection(
            (ip, parts.port or (80 if plain else 443)), timeout)
        if not plain:
            sock = conn._context.wrap_socket(sock, server_hostname=host)
        conn.sock = sock                      # skips conn's own lookup
        conn.request("GET", path, headers=dict(headers or {}))
        resp = conn.getresponse()
        return resp.status, resp.read(MAX_BODY)
    except OSError as exc:
        raise RuntimeError("%s [%s]" % (str(exc)[:160], host))
    finally:
        conn.close()


def open_url(url, headers=None, timeout=12):
    """GET `url`, return the body as text. Tries the system resolver
    first; on a name-resolution failure retries via direct DNS + pinned
    IP. Raises RuntimeError with the failing host in the message."""
    host = urllib.parse.urlsplit(url).hostname or url[:60]
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read(MAX_BODY).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", "replace")[:200].strip()
        except Exception:
            detail = ""
        raise RuntimeError("HTTP %d %s [%s]" % (exc.code, detail, host))
    except (urllib.error.URLError, OSError) as exc:
        reason = str(getattr(exc, "reason", exc) or exc)[:160]
        if not _is_dns_error(exc):
            raise RuntimeError("%s [%s]" % (reason, host))
        # system resolver is broken (e.g. Tailscale MagicDNS without
        # upstreams) — resolve directly and pin the connection to the IP
        try:
            ip = resolve(host)
        except RuntimeError:
            raise RuntimeError("%s [%s]" % (reason, host))
        status, body = _pinned_get(url, headers, ip, timeout)
        if status >= 400:
            raise RuntimeError("HTTP %d [%s]" % (status, host))
        return body.decode("utf-8", "replace")
