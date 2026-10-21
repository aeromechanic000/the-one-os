"""Authentication and session validation (IDEA.md §6.2).

Single user, memory-only password, BOOT_ID + 7-day double validation in
before_request. SECRET_KEY is random per boot, so old cookie signatures fail
immediately after a restart.
"""
import hmac
import time

from flask import jsonify, redirect, request, session

from config import BOOT_ID, PASSWORD, SESSION_MAX_AGE
from app.i18n import t


def verify_password(candidate):
    if not isinstance(candidate, str):
        return False
    return hmac.compare_digest(candidate, PASSWORD)


def login_session():
    """Mark the flask session as authenticated."""
    session.permanent = True
    session["boot_id"] = BOOT_ID
    session["login_at"] = time.time()
    session.permanent_lifetime = SESSION_MAX_AGE


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
    return None
