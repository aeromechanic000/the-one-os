"""Core HTTP routes: login/logout, desktop shell, session info, settings,
terminal list, and the attention-rules REST API (IDEA.md §3.4, §6.2, §7.7).
"""
import json

from flask import Blueprint, jsonify, redirect, render_template, request, \
    session

from app import auth, db
from app import attention_rules
from app.i18n import t
from app.ssh_manager import manager

bp = Blueprint("routes", __name__)

SETTINGS_KEYS = {
    "keyboard_mode",          # windows | mac
    "keyboard_strategy",      # coexist | replace
    "custom_keys",            # JSON: [{label, sequence}]
    "terminal_font_size",     # px number as string
    "office_preview_method",  # libreoffice | frontend | disabled
}
SETTINGS_DEFAULTS = {
    "keyboard_mode": "windows",
    "keyboard_strategy": "coexist",
    "custom_keys": "[]",
    "terminal_font_size": "14",
    "office_preview_method": "libreoffice",
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
    return jsonify({"ok": True})


@bp.get("/auth/logout")
def logout():
    auth.logout_session()
    return redirect("/auth/login")


# --- session / misc -------------------------------------------------------------

@bp.get("/api/session")
def session_info():
    return jsonify(auth.session_info())


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
    return jsonify(out)


@bp.put("/api/settings")
def put_settings():
    data = request.get_json(silent=True) or {}
    for key, value in data.items():
        if key not in SETTINGS_KEYS:
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
        db.set_setting(key, value)
    return get_settings()


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
