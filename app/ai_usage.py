"""AI service usage monitor: stores LLM provider tokens and probes a
config-driven usage/balance endpoint on demand.

Famous providers ship as CONFIGURATION TEMPLATES (provider id, base URL,
auth style, usage endpoint, JSON extract path) — picking one quick-fills
the add form, and every field stays editable. A token can also be defined
fully manually ("custom" provider) and modified or deleted later.

Template base URLs are each provider's ANTHROPIC-COMPATIBLE endpoint (the
one Claude Code itself talks to), and the usage queries mirror cc-switch's
usage monitor: coding-plan quotas for GLM/Kimi/MiniMax, account balances
for DeepSeek/Moonshot, the OAuth usage report for Claude subscriptions
(see https://github.com/farion1231/cc-switch). The MiniMax China account
system serves its quota endpoint from api.minimaxi.com even though
inference moved to api.minimax.cn — same key, both hosts.

Tokens are Fernet-encrypted with the PERSISTED AI-token key (config.py) so
they survive restarts — unlike host passwords, re-entering six API keys
every boot would defeat the feature. The plaintext token never leaves this
module; the client only ever sees the last 4 characters.

What providers actually expose differs wildly (quota percentages for
coding plans, currency balances for pay-as-you-go keys, organization
reports for OpenAI/Anthropic admin keys), so every check returns
normalized `metrics` where possible plus an honest `note`/`error`.
"""
import json
import re
import time
from datetime import datetime

from flask import Blueprint, jsonify, request

from app import db, history, netfetch
from app.i18n import t
from config import AI_TOKEN_FERNET

TIMEOUT = 12        # seconds per upstream probe
MAX_LEN = 512       # cap for URL/label-ish string fields

bp = Blueprint("ai_usage", __name__)


# --- templates ----------------------------------------------------------------

# Every field a template prefills is editable in the add form, so the
# template list is just saved keystrokes, not a hard-coded probe.
# metric_key names the i18n label for the extracted value (quotas are not
# balances); it rides on the provider id, never on the editable form.
PROVIDER_TEMPLATES = {
    "openai": {
        "name_key": "aiu_p_openai",
        "base_url": "https://api.openai.com/v1",
        "auth_style": "bearer",
        "usage_url": "https://api.openai.com/v1/organization/rate_limits",
        "extract_path": "",
    },
    "anthropic": {   # console API key (sk-ant-api03-…)
        "name_key": "aiu_p_anthropic",
        "base_url": "https://api.anthropic.com/v1",
        "auth_style": "x-api-key",
        "usage_url": "",   # org usage needs an Admin key; validate only
        "extract_path": "",
    },
    "claude_code": {   # Claude Pro/Max subscription OAuth token
        "name_key": "aiu_p_claude_code",
        "base_url": "",
        "auth_style": "oauth",
        "usage_url": "https://api.anthropic.com/api/oauth/usage",
        "extract_path": "five_hour.utilization",
        "metric_key": "aiu_5h_used",
        "parser": "claude",
    },
    "deepseek": {
        "name_key": "aiu_p_deepseek",
        "base_url": "https://api.deepseek.com/anthropic",
        "auth_style": "bearer",
        "usage_url": "https://api.deepseek.com/user/balance",
        "extract_path": "balance_infos[0].total_balance",
        "metric_key": "aiu_balance",
    },
    "glm": {   # GLM Coding Plan; pay-as-you-go keys get a "no coding plan" body
        "name_key": "aiu_p_glm",
        "base_url": "https://open.bigmodel.cn/api/anthropic",
        "auth_style": "raw",
        "usage_url": "https://open.bigmodel.cn/api/monitor/usage/quota/limit",
        "extract_path": "data.limits[unit=3].percentage",
        "metric_key": "aiu_5h_used",
        "parser": "glm",
    },
    "glm_intl": {
        "name_key": "aiu_p_glm_intl",
        "base_url": "https://api.z.ai/api/anthropic",
        "auth_style": "raw",
        "usage_url": "https://api.z.ai/api/monitor/usage/quota/limit",
        "extract_path": "data.limits[unit=3].percentage",
        "metric_key": "aiu_5h_used",
        "parser": "glm",
    },
    "kimi": {   # Kimi For Coding
        "name_key": "aiu_p_kimi",
        "base_url": "https://api.kimi.com/coding/",
        "auth_style": "bearer",
        "usage_url": "https://api.kimi.com/coding/v1/usages",
        "extract_path": "usage.remaining",
        "metric_key": "aiu_weekly_left",
        "parser": "kimi",
    },
    "kimi_global": {
        "name_key": "aiu_p_kimi_global",
        "base_url": "https://api.kimi.ai/coding/",
        "auth_style": "bearer",
        "usage_url": "https://api.kimi.ai/coding/v1/usages",
        "extract_path": "usage.remaining",
        "metric_key": "aiu_weekly_left",
        "parser": "kimi",
    },
    "moonshot": {   # Moonshot pay-as-you-go platform key
        "name_key": "aiu_p_moonshot",
        "base_url": "https://api.moonshot.cn/anthropic",
        "auth_style": "bearer",
        "usage_url": "https://api.moonshot.cn/v1/users/me/balance",
        "extract_path": "data.available_balance",
        "metric_key": "aiu_balance",
    },
    "minimax": {   # quota API lives on minimaxi.com even for .cn accounts
        "name_key": "aiu_p_minimax",
        "base_url": "https://api.minimax.cn/anthropic",
        "auth_style": "bearer",
        "usage_url": "https://api.minimaxi.com/v1/api/openplatform/coding_plan/remains",
        "extract_path":
            "model_remains[model_name=general].current_interval_remaining_percent",
        "metric_key": "aiu_5h_left",
        "parser": "minimax",
    },
    "minimax_intl": {
        "name_key": "aiu_p_minimax_intl",
        "base_url": "https://api.minimax.io/anthropic",
        "auth_style": "bearer",
        "usage_url": "https://api.minimax.io/v1/api/openplatform/coding_plan/remains",
        "extract_path":
            "model_remains[model_name=general].current_interval_remaining_percent",
        "metric_key": "aiu_5h_left",
        "parser": "minimax",
    },
}

AUTH_STYLES = ("bearer", "x-api-key", "raw", "oauth")

EDITABLE_FIELDS = ("label", "base_url", "auth_style", "usage_url",
                   "extract_path")


def provider_meta():
    return [{"id": pid, "name": t(meta["name_key"]),
             **{k: meta[k] for k in
                ("base_url", "auth_style", "usage_url", "extract_path")}}
            for pid, meta in PROVIDER_TEMPLATES.items()]


# --- HTTP + extraction helpers ---------------------------------------------------

def _get_json(url, headers):
    """GET and parse JSON; raises RuntimeError with a short reason.
    Transport goes through app/netfetch.py, which retries past a broken
    system resolver (Tailscale MagicDNS) via direct DNS."""
    raw = netfetch.open_url(url, headers, TIMEOUT)
    try:
        return json.loads(raw)
    except ValueError:
        raise RuntimeError("non-JSON response")


def _auth_headers(cfg, token):
    if cfg["auth_style"] == "x-api-key":
        return {"x-api-key": token, "anthropic-version": "2023-06-01"}
    if cfg["auth_style"] == "raw":
        # Zhipu monitor endpoints take the bare key, no Bearer prefix
        return {"Authorization": token}
    if cfg["auth_style"] == "oauth":
        # Anthropic's OAuth usage report (Claude Code subscriptions)
        return {"Authorization": "Bearer " + token,
                "anthropic-beta": "oauth-2025-04-20"}
    return {"Authorization": "Bearer " + token}


def _models_url(base):
    """Derive the key-validation models list from a base URL. Bases that
    already end in a version segment (OpenAI style .../v1) take /models;
    Anthropic-compatible bases (.../anthropic, .../coding/) take /v1/models."""
    base = (base or "").rstrip("/")
    if not base:
        return ""
    if re.search(r"/v\d+$", base):
        return base + "/models"
    return base + "/v1/models"


def _extract(data, path):
    """Walk a dotted path like balance_infos[0].total_balance. A bracket
    holding digits indexes a list item; [field=value] picks the first list
    item whose field equals the value (e.g. model_remains[model_name=general]).
    Returns (found, value)."""
    node = data
    for part in [p for p in path.split(".") if p]:
        key, idx, filt = part, -1, None
        if part.endswith("]") and "[" in part:
            key, _, tail = part.partition("[")
            tail = tail[:-1]
            if "=" in tail:
                field, _, val = tail.partition("=")
                filt = (field, val)
            else:
                try:
                    idx = int(tail)
                except ValueError:
                    return False, None
        if not isinstance(node, dict) or key not in node:
            return False, None
        node = node[key]
        if filt is not None:
            field, val = filt
            if not isinstance(node, list):
                return False, None
            node = next((item for item in node
                         if isinstance(item, dict)
                         and str(item.get(field)) == val), None)
            if node is None:
                return False, None
        elif idx >= 0:
            if isinstance(node, list) and 0 <= idx < len(node):
                node = node[idx]
            else:
                return False, None
    return True, node


def _fmt(value):
    if isinstance(value, (dict, list)):
        return json.dumps(value)[:160]
    return str(value)


# --- provider quota parsers --------------------------------------------------------
# Templates whose usage endpoint returns windowed quotas (coding plans,
# subscriptions) parse into one percentage metric per window, mirroring the
# providers' own dashboards. A parser that yields nothing falls back to the
# generic extract path, so edited and custom configs keep working.

def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _fmt_pct(v):
    return ("%.1f" % v).rstrip("0").rstrip(".") + "%"


def _reset_str(value):
    """'resets in 2h13m' from an epoch (seconds or ms) or ISO string."""
    seconds = None
    if isinstance(value, (int, float)) and value > 0:
        seconds = value / 1000.0 if value >= 1e12 else float(value)
    elif isinstance(value, str) and value[:2] == "20":
        try:
            seconds = datetime.fromisoformat(
                value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return ""
    if not seconds:
        return ""
    minutes = int((seconds - time.time()) / 60)
    if minutes <= 0:
        return ""
    h, m = divmod(minutes, 60)
    span = ("%dh%02dm" % (h, m)) if h else "%dm" % m
    return t("aiu_resets_in", span=span)


def _quota_metric(pct, reset, window):
    value = _fmt_pct(pct)
    reset = _reset_str(reset)
    if reset:
        value += " · " + reset
    return _metric(t("aiu_used"), value, window=window)


def _parse_kimi(data):
    """Kimi /coding/v1/usages comes in overlapping shapes:
    - usages: {limit_5h: {used_ratio, reset_time}, limit_7d: {...}} — explicit
      ratios (newest shape; preferred when present)
    - limits: [{detail: {limit, used|remaining, resetTime}}] — the 5-hour
      window; `remaining` is omitted when it hits zero (exhausted window),
      so `used` is authoritative when present
    - usage: {limit, used|remaining, resetTime} — the weekly window"""
    metrics = []
    if not isinstance(data, dict):
        return metrics
    us = data.get("usages")
    if isinstance(us, dict):
        for key, window in (("limit_5h", "5h"), ("limit_7d", "7d")):
            w = us.get(key)
            if isinstance(w, dict) and _num(w.get("used_ratio")) is not None:
                metrics.append(_quota_metric(
                    _num(w["used_ratio"]) * 100.0,
                    w.get("reset_time"), window))
        if metrics:
            return metrics
    limits = data.get("limits")
    if isinstance(limits, list):
        for entry in limits:
            d = entry.get("detail") if isinstance(entry, dict) else None
            if not isinstance(d, dict):
                continue
            limit, used = _num(d.get("limit")), _num(d.get("used"))
            remaining = _num(d.get("remaining"))
            if not limit or limit <= 0:
                continue
            if used is None and remaining is not None:
                used = limit - remaining
            if used is not None:
                metrics.append(_quota_metric(used / limit * 100.0,
                                             d.get("resetTime"), "5h"))
                break
    u = data.get("usage")
    if isinstance(u, dict):
        limit, used = _num(u.get("limit")), _num(u.get("used"))
        remaining = _num(u.get("remaining"))
        if limit and limit > 0:
            if used is None and remaining is not None:
                used = limit - remaining
            if used is not None:
                metrics.append(_quota_metric(used / limit * 100.0,
                                             u.get("resetTime"), "7d"))
    return metrics


def _parse_claude(data):
    """{five_hour: {utilization, resets_at}, seven_day: {...}} — percents."""
    metrics = []
    if not isinstance(data, dict):
        return metrics
    for key, window in (("five_hour", "5h"), ("seven_day", "7d")):
        w = data.get(key)
        if isinstance(w, dict) and _num(w.get("utilization")) is not None:
            metrics.append(_quota_metric(_num(w["utilization"]),
                                         w.get("resets_at"), window))
    return metrics


def _parse_minimax(data):
    """model_remains[] — the model_name=general entry; remaining percents."""
    metrics = []
    if not isinstance(data, dict):
        return metrics
    remains = data.get("model_remains")
    item = next((r for r in (remains or []) if isinstance(r, dict)
                 and r.get("model_name") == "general"), None)
    if not item:
        return metrics
    pct = _num(item.get("current_interval_remaining_percent"))
    if pct is not None:
        metrics.append(_quota_metric(100.0 - pct,
                                     item.get("end_time"), "5h"))
    if item.get("current_weekly_status") == 1:
        wpct = _num(item.get("current_weekly_remaining_percent"))
        if wpct is not None:
            metrics.append(_quota_metric(100.0 - wpct,
                                         item.get("weekly_end_time"), "7d"))
    return metrics


def _parse_glm(data):
    """data.limits[] — unit 3 = 5-hour window, unit 6 = weekly; used %.
    Entries without a recognizable unit fall back to the reset-time
    heuristic cc-switch uses: no reset time -> 5-hour first, then earliest
    reset, filling whichever window slot is still empty."""
    metrics = []
    if not isinstance(data, dict):
        return metrics
    data = data.get("data")
    limits = data.get("limits") if isinstance(data, dict) else None
    by_unit, unclassified = {}, []
    for item in (limits or []):
        if not isinstance(item, dict):
            continue
        if str(item.get("type", "")).upper() not in ("TOKENS_LIMIT",
                                                     "CREDIT_LIMIT"):
            continue
        unit = item.get("unit")
        if unit in (3, 6) and unit not in by_unit:
            by_unit[unit] = item
        else:
            unclassified.append(item)

    def reset_key(it):
        r = it.get("nextResetTime")
        has = isinstance(r, (int, float)) and r > 0
        return (has, r if isinstance(r, (int, float)) else 0)

    for item in sorted(unclassified, key=reset_key):
        if 3 not in by_unit:
            by_unit[3] = item
        elif 6 not in by_unit:
            by_unit[6] = item
    for unit, window in ((3, "5h"), (6, "7d")):
        item = by_unit.get(unit)
        if item and _num(item.get("percentage")) is not None:
            metrics.append(_quota_metric(_num(item["percentage"]),
                                         item.get("nextResetTime"), window))
    return metrics


QUOTA_PARSERS = {
    "kimi": _parse_kimi,
    "claude": _parse_claude,
    "minimax": _parse_minimax,
    "glm": _parse_glm,
}


def _with_dns_hint(text):
    """Append an actionable hint when the failure is hostname resolution."""
    low = text.lower()
    if any(m in low for m in ("name or service not known", "getaddrinfo",
                              "name does not resolve",
                              "temporary failure in name resolution")):
        return "%s %s" % (text, t("aiu_dns_hint"))
    return text


# --- generic probe ----------------------------------------------------------------

def probe(cfg, token, metric_key="", parser=""):
    """Run one usage check against the token's config. Never raises."""
    metrics, note = [], ""
    headers = _auth_headers(cfg, token)

    if cfg["usage_url"]:
        try:
            data = _get_json(cfg["usage_url"], headers)
            parsed = QUOTA_PARSERS[parser](data) if parser in QUOTA_PARSERS \
                else []
            if parsed:
                metrics.extend(parsed)
            elif cfg["extract_path"]:
                found, value = _extract(data, cfg["extract_path"])
                if found:
                    metrics.append(_metric(t(metric_key or "aiu_usage"),
                                           _fmt(value)))
                else:
                    metrics.append(_metric(
                        t(metric_key or "aiu_usage"), _fmt(data)))
                    note = t("aiu_extract_missed")
            else:
                metrics.append(_metric(t("aiu_usage"), _fmt(data)))
        except RuntimeError as exc:
            note = "%s %s" % (t("aiu_usage_failed"),
                              _with_dns_hint(str(exc)))

    # key validation against the models list (cheap and universal)
    models = _models_url(cfg["base_url"])
    if models:
        try:
            _get_json(models, headers)
            metrics.append(_metric(t("aiu_key_valid"), "OK"))
        except RuntimeError as exc:
            if not metrics:
                return _fail(_with_dns_hint(str(exc)))
            note = (note + " " if note else "") + \
                "%s %s" % (t("aiu_validate_failed"),
                           _with_dns_hint(str(exc)))

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
    template = PROVIDER_TEMPLATES.get(row["provider"], {})
    try:
        result = probe(cfg, token, template.get("metric_key", ""),
                       template.get("parser", ""))
    except Exception as exc:   # probe guards itself; belt and braces
        result = _fail(str(exc))
    db.execute(
        "UPDATE ai_tokens SET last_check = ?, "
        "last_check_at = datetime('now') WHERE id = ?",
        (json.dumps(result), tid))
    return jsonify(result)
