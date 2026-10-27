"""AI service usage monitor: stores LLM provider tokens and probes a
config-driven usage/balance endpoint on demand.

Famous providers ship as CONFIGURATION TEMPLATES (provider id, base URL,
auth style, usage endpoint, JSON extract path) — picking one quick-fills
the add form, and every field stays editable. A token can also be defined
fully manually ("custom" provider) and modified or deleted later.

Tokens are Fernet-encrypted with the PERSISTED AI-token key (config.py) so
they survive restarts — unlike host passwords, re-entering six API keys
every boot would defeat the feature. The plaintext token never leaves this
module; the client only ever sees the last 4 characters.

What providers actually expose differs wildly (balance for DeepSeek/Kimi,
organization reports for OpenAI/Anthropic admin keys, nothing public for
MiniMax), so every check returns normalized `metrics` where possible plus
an honest `note`/`error`. Subscription-style 5-hour/weekly limits are not
API-exposed by any provider; windowed values pass through with labels.
"""
import json
import time
import urllib.error
import urllib.request

from flask import Blueprint, jsonify, request

from app import db, history
from app.i18n import t
from config import AI_TOKEN_FERNET

TIMEOUT = 12        # seconds per upstream probe
MAX_LEN = 512       # cap for URL/label-ish string fields

bp = Blueprint("ai_usage", __name__)


# --- templates ----------------------------------------------------------------

# Every field a template prefills is editable in the add form, so the
# template list is just saved keystrokes, not a hard-coded probe.
PROVIDER_TEMPLATES = {
    "openai": {
        "name_key": "aiu_p_openai",
        "base_url": "https://api.openai.com/v1",
        "auth_style": "bearer",
        "usage_url": "https://api.openai.com/v1/organization/rate_limits",
        "extract_path": "",
    },
    "anthropic": {
        "name_key": "aiu_p_anthropic",
        "base_url": "https://api.anthropic.com/v1",
        "auth_style": "x-api-key",
        "usage_url": "",   # org usage needs an Admin key; validate only
        "extract_path": "",
    },
    "deepseek": {
        "name_key": "aiu_p_deepseek",
        "base_url": "https://api.deepseek.com/v1",
        "auth_style": "bearer",
        "usage_url": "https://api.deepseek.com/user/balance",
        "extract_path": "balance_infos[0].total_balance",
    },
    "glm": {
        "name_key": "aiu_p_glm",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "auth_style": "bearer",
        "usage_url": "",
        "extract_path": "",
    },
    "kimi": {
        "name_key": "aiu_p_kimi",
        "base_url": "https://api.moonshot.cn/v1",
        "auth_style": "bearer",
        "usage_url": "https://api.moonshot.cn/v1/users/me/balance",
        "extract_path": "data.available_balance",
    },
    "minimax": {
        "name_key": "aiu_p_minimax",
        "base_url": "https://api.minimax.chat/v1",
        "auth_style": "bearer",
        "usage_url": "",
        "extract_path": "",
    },
}

AUTH_STYLES = ("bearer", "x-api-key")

EDITABLE_FIELDS = ("label", "base_url", "auth_style", "usage_url",
                   "extract_path")


def provider_meta():
    return [{"id": pid, "name": t(meta["name_key"]),
             **{k: meta[k] for k in
                ("base_url", "auth_style", "usage_url", "extract_path")}}
            for pid, meta in PROVIDER_TEMPLATES.items()]


# --- HTTP + extraction helpers ---------------------------------------------------

def _get_json(url, headers):
    """GET and parse JSON; raises RuntimeError with a short reason."""
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read(262144).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", "replace")[:200]
        except Exception:
            detail = ""
        raise RuntimeError("HTTP %d %s" % (exc.code, detail.strip()))
    except (urllib.error.URLError, OSError) as exc:
        raise RuntimeError(str(exc)[:200])
    try:
        return json.loads(raw)
    except ValueError:
        raise RuntimeError("non-JSON response")


def _auth_headers(cfg, token):
    if cfg["auth_style"] == "x-api-key":
        return {"x-api-key": token, "anthropic-version": "2023-06-01"}
    return {"Authorization": "Bearer " + token}


def _extract(data, path):
    """Walk a dotted path like balance_infos[0].total_balance.
    Returns (found, value)."""
    node = data
    for part in [p for p in path.split(".") if p]:
        idx = -1
        if part.endswith("]") and "[" in part:
            part, _, tail = part.partition("[")
            try:
                idx = int(tail.rstrip("]"))
            except ValueError:
                return False, None
        if not isinstance(node, dict) or part not in node:
            return False, None
        node = node[part]
        if idx >= 0:
            if isinstance(node, list) and 0 <= idx < len(node):
                node = node[idx]
            else:
                return False, None
    return True, node


def _fmt(value):
    if isinstance(value, (dict, list)):
        return json.dumps(value)[:160]
    return str(value)


# --- generic probe ----------------------------------------------------------------

def probe(cfg, token):
    """Run one usage check against the token's config. Never raises."""
    metrics, note = [], ""
    headers = _auth_headers(cfg, token)

    if cfg["usage_url"]:
        try:
            data = _get_json(cfg["usage_url"], headers)
            if cfg["extract_path"]:
                found, value = _extract(data, cfg["extract_path"])
                if found:
                    metrics.append(_metric(t("aiu_balance"), _fmt(value)))
                else:
                    metrics.append(_metric(
                        t("aiu_balance"), _fmt(data)))
                    note = t("aiu_extract_missed")
            else:
                metrics.append(_metric(t("aiu_usage"), _fmt(data)))
        except RuntimeError as exc:
            note = "%s %s" % (t("aiu_usage_failed"), str(exc))

    # key validation against the models list (cheap and universal)
    base = (cfg["base_url"] or "").rstrip("/")
    if base:
        try:
            _get_json(base + "/models", headers)
            metrics.append(_metric(t("aiu_key_valid"), "OK"))
        except RuntimeError as exc:
            if not metrics:
                return _fail(str(exc))
            note = (note + " " if note else "") + \
                "%s %s" % (t("aiu_validate_failed"), str(exc))

    if not metrics:
        note = note or t("aiu_no_usage_api")
    return _ok(metrics, note.strip())


def _metric(label, value, window=""):
    return {"label": label, "value": str(value), "window": window}


def _ok(metrics, note=""):
    return {"ok": True, "checked_at": time.time(),
            "metrics": metrics, "note": note}


def _fail(error):
    return {"ok": False, "checked_at": time.time(), "error": error[:300]}


# --- storage -----------------------------------------------------------------------

CONFIG_FIELDS = EDITABLE_FIELDS + ("provider",)


def _clean_config(data, defaults):
    """Merge submitted fields over template defaults; returns config dict."""
    cfg = dict(defaults)
    for key in EDITABLE_FIELDS:
        if key in data:
            cfg[key] = str(data.get(key) or "").strip()[:MAX_LEN]
    if cfg["auth_style"] not in AUTH_STYLES:
        cfg["auth_style"] = "bearer"
    return cfg


def _template_defaults(provider):
    meta = PROVIDER_TEMPLATES.get(provider)
    if meta:
        return {
            "label": t(meta["name_key"]),
            "base_url": meta["base_url"],
            "auth_style": meta["auth_style"],
            "usage_url": meta["usage_url"],
            "extract_path": meta["extract_path"],
        }
    return {"label": "", "base_url": "", "auth_style": "bearer",
            "usage_url": "", "extract_path": ""}


def _public(row):
    out = {k: row[k] for k in ("id", "provider", "label", "base_url",
                               "auth_style", "usage_url", "extract_path",
                               "last4", "created_at", "last_check_at")}
    try:
        out["last_check"] = json.loads(row["last_check"] or "null")
    except ValueError:
        out["last_check"] = None
    return out


def _row(tid):
    return db.query_one("SELECT * FROM ai_tokens WHERE id = ?", (tid,))


# --- REST -------------------------------------------------------------------------

@bp.get("/api/ai/tokens")
def list_tokens():
    rows = db.query("SELECT * FROM ai_tokens ORDER BY id")
    return jsonify({"providers": provider_meta(),
                    "tokens": [_public(r) for r in rows]})


@bp.post("/api/ai/tokens")
def add_token():
    data = request.get_json(silent=True) or {}
    provider = str(data.get("provider") or "custom")
    if provider != "custom" and provider not in PROVIDER_TEMPLATES:
        return jsonify({"error": t("aiu_bad_provider")}), 400
    token = str(data.get("token") or "").strip()
    if not token:
        return jsonify({"error": t("aiu_bad_token")}), 400
    cfg = _clean_config(data, _template_defaults(provider))
    if not cfg["label"]:
        cfg["label"] = provider if provider != "custom" else t("aiu_custom")
    enc = AI_TOKEN_FERNET.encrypt(token.encode()).decode()
    tid = db.execute(
        "INSERT INTO ai_tokens(provider, label, base_url, auth_style, "
        "usage_url, extract_path, token_enc, last4) "
        "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
        (provider, cfg["label"], cfg["base_url"], cfg["auth_style"],
         cfg["usage_url"], cfg["extract_path"], enc, token[-4:]))
    history.record("ai_token_add", "", cfg["label"])
    return jsonify(_public(_row(tid)))


@bp.put("/api/ai/tokens/<int:tid>")
def update_token(tid):
    row = _row(tid)
    if not row:
        return jsonify({"error": t("aiu_not_found")}), 404
    data = request.get_json(silent=True) or {}
    cfg = _clean_config(data, {k: row[k] for k in EDITABLE_FIELDS})
    sets, params = [], []
    for key in EDITABLE_FIELDS:
        sets.append("%s = ?" % key)
        params.append(cfg[key])
    # optional token replacement: empty means keep the stored one
    new_token = str(data.get("token") or "").strip()
    if new_token:
        sets.append("token_enc = ?")
        params.append(AI_TOKEN_FERNET.encrypt(
            new_token.encode()).decode())
        sets.append("last4 = ?")
        params.append(new_token[-4:])
    params.append(tid)
    db.execute("UPDATE ai_tokens SET %s WHERE id = ?" % ", ".join(sets),
               tuple(params))
    history.record("ai_token_update", "", cfg["label"])
    return jsonify(_public(_row(tid)))


@bp.delete("/api/ai/tokens/<int:tid>")
def delete_token(tid):
    row = _row(tid)
    if not row:
        return jsonify({"error": t("aiu_not_found")}), 404
    db.execute("DELETE FROM ai_tokens WHERE id = ?", (tid,))
    history.record("ai_token_delete", "", row["label"])
    return jsonify({"ok": True})


@bp.post("/api/ai/tokens/<int:tid>/check")
def check_token(tid):
    row = _row(tid)
    if not row:
        return jsonify({"error": t("aiu_not_found")}), 404
    try:
        token = AI_TOKEN_FERNET.decrypt(row["token_enc"].encode()).decode()
    except Exception:
        return jsonify({"error": t("aiu_key_needs_reentry")}), 500
    cfg = {k: row[k] for k in EDITABLE_FIELDS}
    try:
        result = probe(cfg, token)
    except Exception as exc:   # probe guards itself; belt and braces
        result = _fail(str(exc))
    db.execute(
        "UPDATE ai_tokens SET last_check = ?, "
        "last_check_at = datetime('now') WHERE id = ?",
        (json.dumps(result), tid))
    return jsonify(result)
