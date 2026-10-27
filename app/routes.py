"""Core HTTP routes: login/logout, desktop shell, session info, settings,
terminal list, and the attention-rules REST API (IDEA.md §3.4, §6.2, §7.7).
"""
import json

from flask import Blueprint, jsonify, redirect, render_template, request, \
    session

from app import activation, ai, auth, db, history
from app import attention_rules
from app.i18n import t
from app.ssh_manager import manager
from config import FERNET

bp = Blueprint("routes", __name__)

SETTINGS_KEYS = {
    "keyboard_mode",          # windows | mac
    "keyboard_strategy",      # coexist | replace
    "custom_keys",            # JSON: [{label, sequence}]
    "terminal_font_size",     # px number as string
    "office_preview_method",  # libreoffice | frontend | disabled
    "ai_base_url",            # OpenAI-compatible API base, e.g. https://x/v1
    "ai_model",               # model id string
    "ai_api_key_enc",         # Fernet-encrypted; written via ai_api_key only
    "history_days",           # operation-history retention, days
    "max_single_file_mb",     # single-file cap for transfers/uploads/preview
    "max_storage_mb",         # total storage/ quota for the file browser
    "theme",                  # color theme id (registry in static/js/theme.js)
}
SETTINGS_DEFAULTS = {
    "keyboard_mode": "windows",
    "keyboard_strategy": "coexist",
    "custom_keys": "[]",
    "terminal_font_size": "14",
    "office_preview_method": "libreoffice",
    "ai_base_url": "",
    "ai_model": "",
    "ai_api_key_enc": "",
    "history_days": "90",
    "max_single_file_mb": "512",
    "max_storage_mb": "2048",
    "theme": "midnight",
}

# keep in sync with THEMES in static/js/theme.js
THEME_IDS = {
    "midnight", "dracula", "nord", "tokyo", "solar-dark",
    "gruvbox", "solar-light", "daylight",
}


# --- pages -------------------------------------------------------------------

@bp.get("/")
def index():
    if not auth.session_valid():
        return redirect("/auth/login")
    from app import preview as preview_mod
    return render_template("desktop.html", office_available=preview_mod.office_available())


@bp.get("/auth/login")
def login_page():
    return render_template("login.html")


@bp.post("/auth/login")
def login_post():
    data = request.get_json(silent=True) or {}
    if not auth.verify_password(data.get("password", "")):
        return jsonify({"error": t("login_invalid")}), 401
    auth.login_session()
    # a fresh login takes over as the active frontend (IDEA.md single-user,
    # multi-device: latest login wins, every other frontend falls asleep)
    activation.register(session["client_id"], session["client_addr"])
    history.record("login", "", "signed in from " + session["client_addr"])
    return jsonify({"ok": True})


@bp.get("/auth/logout")
def logout():
    activation.clear(session.get("client_id"))
    auth.logout_session()
    return redirect("/auth/login")


# --- session / misc -------------------------------------------------------------

@bp.get("/api/session")
def session_info():
    return jsonify(auth.session_info())


@bp.get("/api/activation")
def activation_state():
    """Who is the active frontend, and is it this one?"""
    client_id, addr = auth.ensure_client()
    return jsonify(activation.state_for(client_id, addr))


@bp.post("/api/activation/wake")
def activation_wake():
    """Take over as the active frontend from a sleeping one."""
    client_id, _addr = auth.ensure_client()
    addr = session.get("client_addr") or auth.client_ip()
    session["client_addr"] = addr
    activation.register(client_id, addr)
    history.record("wake", "", "frontend took over from " + addr)
    return jsonify(activation.state_for(client_id, addr))


@bp.get("/api/terminals")
def terminals():
    return jsonify(manager.list_terminals())


@bp.get("/api/settings")
def get_settings():
    out = dict(SETTINGS_DEFAULTS)
    for key in SETTINGS_KEYS:
        stored = db.get_setting(key, None)
        if stored is not None:
            out[key] = stored
    try:
        out["custom_keys"] = json.loads(out.get("custom_keys") or "[]")
    except ValueError:
        out["custom_keys"] = []
    # never leak the API key ciphertext; expose set/re-entry flags instead
    enc = out.pop("ai_api_key_enc", "")
    out["ai_api_key_set"] = bool(enc)
    if enc:
        try:
            FERNET.decrypt(enc.encode())
        except Exception:
            out["ai_api_key_needs_reentry"] = True
    return jsonify(out)


@bp.put("/api/settings")
def put_settings():
    data = request.get_json(silent=True) or {}
    changed = []
    # plaintext key arrives as ai_api_key and is stored encrypted
    if "ai_api_key" in data:
        key = str(data.get("ai_api_key") or "").strip()
        if key:
            db.set_setting("ai_api_key_enc",
                           FERNET.encrypt(key.encode()).decode())
        else:
            db.set_setting("ai_api_key_enc", "")
        changed.append("ai_api_key")
    for key, value in data.items():
        if key not in SETTINGS_KEYS or key == "ai_api_key_enc":
            continue
        if key == "custom_keys":
            if not isinstance(value, list):
                continue
            clean = []
            for item in value[:24]:
                if isinstance(item, dict) and item.get("label") and \
                        item.get("sequence"):
                    clean.append({"label": str(item["label"])[:16],
                                  "sequence": str(item["sequence"])[:256]})
            value = json.dumps(clean)
        elif key == "keyboard_mode" and value not in ("windows", "mac"):
            continue
        elif key == "theme" and value not in THEME_IDS:
            continue
        elif key == "keyboard_strategy" and value not in ("coexist", "replace"):
            continue
        elif key == "office_preview_method" and \
                value not in ("libreoffice", "frontend", "disabled"):
            continue
        elif key == "terminal_font_size":
            try:
                fs = int(value)
                if not 8 <= fs <= 32:
                    continue
            except (TypeError, ValueError):
                continue
        elif key == "ai_base_url":
            url = str(value or "").strip()
            if url and not (url.startswith("http://") or
                            url.startswith("https://")):
                continue
            value = url[:512]
        elif key == "ai_model":
            value = str(value or "").strip()[:200]
        elif key in ("history_days", "max_single_file_mb", "max_storage_mb"):
            try:
                n = int(value)
            except (TypeError, ValueError):
                continue
            if key == "history_days":
                if not 1 <= n <= 3650:
                    continue
            elif not 1 <= n <= 102400:
                continue
            value = str(n)
        db.set_setting(key, value)
        changed.append(key)
    if changed:
        history.record("settings_update", "", "updated: " + ", ".join(changed))
    return get_settings()


# --- AI assistant & operation history ----------------------------------------

@bp.post("/api/ai/chat")
def ai_chat():
    data = request.get_json(silent=True) or {}
    messages = data.get("messages")
    if not isinstance(messages, list) or not messages:
        return jsonify({"error": "messages required"}), 400
    try:
        reply = ai.chat(messages)
    except ai.AIError as exc:
        return jsonify({"error": str(exc)}), exc.status
    first_user = next((str(m.get("content") or "")
                       for m in messages
                       if isinstance(m, dict) and m.get("role") == "user"), "")
    history.record("ai_chat", "",
                   ("asked: " + first_user)[:120] if first_user else "chat")
    return jsonify({"reply": reply})


@bp.get("/api/history")
def history_entries():
    days = request.args.get("days", 7, type=int) or 7
    limit = request.args.get("limit", 500, type=int) or 500
    return jsonify(history.list_history(days=days, limit=limit))


# --- attention rules REST (IDEA.md §7.7) ---------------------------------------------

@bp.get("/api/attention/rules")
def rules_list():
    return jsonify(attention_rules.list_rules())


@bp.post("/api/attention/rules")
def rules_create():
    data = request.get_json(silent=True) or {}
    try:
        rid = attention_rules.create_rule(data)
    except attention_rules.ValidationError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"id": rid}), 201


@bp.put("/api/attention/rules/<int:rule_id>")
def rules_update(rule_id):
    data = request.get_json(silent=True) or {}
    try:
        attention_rules.update_rule(rule_id, data)
    except attention_rules.ValidationError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True})


@bp.delete("/api/attention/rules/<int:rule_id>")
def rules_delete(rule_id):
    try:
        attention_rules.delete_rule(rule_id)
    except attention_rules.ValidationError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True})


@bp.post("/api/attention/rules/test")
def rules_test():
    data = request.get_json(silent=True) or {}
    rule = data.get("rule") or {}
    sample = data.get("sample") or ""
    if not sample:
        return jsonify({"error": "sample is required"}), 400
    try:
        return jsonify(attention_rules.test_rule(rule, sample))
    except attention_rules.ValidationError as exc:
        return jsonify({"error": str(exc)}), 400


@bp.get("/api/attention/rules/export")
def rules_export():
    return jsonify(attention_rules.export_rules())


@bp.post("/api/attention/rules/import")
def rules_import():
    payload = request.get_json(silent=True) or {}
    try:
        return jsonify(attention_rules.import_rules(payload))
    except attention_rules.ValidationError as exc:
        return jsonify({"error": str(exc)}), 400
