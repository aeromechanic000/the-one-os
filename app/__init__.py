"""Application factory (IDEA.md §6.1)."""
import os

from config import (PREVIEW_CACHE_DIR, REMOTE_CACHE_DIR, STORAGE_DIR)
from app.i18n import t


def _ensure_dirs():
    os.makedirs(STORAGE_DIR, mode=0o700, exist_ok=True)
    try:
        os.chmod(STORAGE_DIR, 0o700)  # makedirs mode is masked on some systems
    except OSError:
        pass
    os.makedirs(PREVIEW_CACHE_DIR, exist_ok=True)
    os.makedirs(REMOTE_CACHE_DIR, exist_ok=True)


def create_app():
    _ensure_dirs()

    from flask import Flask
    import config
    from app.db import init_db

    init_db()

    from app import actions, attention_rules
    actions.seed_builtins()
    attention_rules.seed_builtins()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    app = Flask(__name__,
                template_folder=os.path.join(root, "templates"),
                static_folder=os.path.join(root, "static"))
    app.secret_key = config.SECRET_KEY
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        MAX_CONTENT_LENGTH=4 * 1024 * 1024 * 1024,  # large file uploads
    )
    app.jinja_env.globals["t"] = t

    # window.I18N for the frontend, kept identical to the backend dictionary
    import json as _json
    from app.i18n import EN

    @app.context_processor
    def _inject_i18n():
        return {"I18N_JSON": _json.dumps(EN)}

    from app.auth import before_request_hook
    app.before_request(before_request_hook)

    from app import (actions as actions_bp, ai_usage, attention_rules, files,
                     files_browser, hosts, preview, routes, sockets)
    app.register_blueprint(routes.bp)
    app.register_blueprint(hosts.bp)
    app.register_blueprint(actions_bp.bp)
    app.register_blueprint(ai_usage.bp)
    app.register_blueprint(files.bp)
    app.register_blueprint(files_browser.bp)
    app.register_blueprint(preview.bp)

    sockets.init_socketio(app)
    return app


_background_started = False


def start_background():
    """Start monitor + cleanup threads and the transfer queue worker.

    Safe to call multiple times. Callers decide whether the current process
    is the serving one (main.py guards the werkzeug reloader parent away).
    """
    global _background_started
    if _background_started:
        return
    _background_started = True
    from app import cleanup, files, locality, monitor
    monitor.start()
    cleanup.start()
    files.start_worker()
    locality.recheck_all_async()  # re-mark hosts pointing at this machine
