"""Authentication and session validation (IDEA.md §6.2).

Single user, memory-only password, BOOT_ID + 7-day double validation in
before_request. SECRET_KEY and BOOT_ID persist across restarts (config.py),
so a login survives service restarts up to its 7-day cap.

Every logged-in browser also carries a frontend identity (client_id +
client_addr) used by app/activation.py to enforce the single-active-
frontend rule: a deactivated frontend may read state but not change it.
"""
import hmac
import secrets
import time

from flask import jsonify, redirect, request, session

from config import BOOT_ID, PASSWORD, SESSION_MAX_AGE
from app.i18n import t


def verify_password(candidate):
    if not isinstance(candidate, str):
        return False
    return hmac.compare_digest(candidate, PASSWORD)


def client_ip():
    """Address of the calling machine as this server sees it (a proxy hop
    is honored so the top bar shows the real client behind e.g. nginx)."""
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def ensure_client():
    """Give the session a frontend identity; cookies from before this
    feature existed get one lazily (but are not made active)."""
    if "client_id" not in session:
        session["client_id"] = secrets.token_urlsafe(9)
        session["client_addr"] = client_ip()
    return session["client_id"], session.get("client_addr", "")


def login_session():
    """Mark the flask session as authenticated. A fresh login mints a new
    frontend identity — logging in is also a takeover."""
    session.permanent = True
    session["boot_id"] = BOOT_ID
    session["login_at"] = time.time()
    session["client_id"] = secrets.token_urlsafe(9)
    session["client_addr"] = client_ip()


def logout_session():
    session.clear()


def session_valid():
    if session.get("boot_id") != BOOT_ID:
        return False
    login_at = session.get("login_at", 0)
    if not login_at or (time.time() - login_at) > SESSION_MAX_AGE:
        return False
    return True


def session_info():
    login_at = session.get("login_at", 0)
    remaining = max(0, int(SESSION_MAX_AGE - (time.time() - login_at)))
    return {
        "login_at": login_at,
        "remaining_seconds": remaining,
        "max_seconds": SESSION_MAX_AGE,
    }


def before_request_hook():
    """Gate every request except the login page and static assets."""
    path = request.path
    if path == "/auth/login" or path.startswith("/static") or path == "/favicon.ico":
        return None
    if not session_valid():
        if path.startswith("/api/") or path.startswith("/socket.io"):
            return jsonify({"error": t("session_expired")}), 401
        return redirect("/auth/login")
    ensure_client()
    # Single-active frontend: a deactivated client may still read (so its
    # sleep card can render and its mirror stays current) but not mutate.
    # /socket.io is exempt so its connection survives for wake pushes;
    # state-changing socket events are guarded individually in sockets.py.
    if (request.method not in ("GET", "HEAD", "OPTIONS")
            and not path.startswith("/socket.io")
            and path != "/api/activation/wake"):
        from app import activation
        if not activation.is_active(session.get("client_id")):
            return jsonify({"error": t("err_deactivated")}), 409
    return None
