"""AI assistant proxy: talks to any OpenAI-compatible chat completions API
(base_url + model + optional api_key, all from Settings; stdlib urllib only).

A digest of the last 7 days of operation history is injected as a system
message so the assistant can answer questions like "summarize my work in the
last 7 days" without any tool calling.
"""
import json
import urllib.error
import urllib.request

from app import db, history
from app.i18n import t

HISTORY_DAYS = 7           # context window injected into every chat
MAX_MESSAGES = 24          # conversation tail sent upstream (keep-last)
MAX_MESSAGE_CHARS = 8000   # per message
TIMEOUT = 120              # seconds for the upstream call


class AIError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


def _config():
    """(base_url, model, api_key) from settings; raises AIError if unusable."""
    base_url = (db.get_setting("ai_base_url", "") or "").strip()
    model = (db.get_setting("ai_model", "") or "").strip()
    if not base_url or not model:
        raise AIError(t("ai_not_configured"), status=400)
    key = ""
    enc = db.get_setting("ai_api_key_enc", "")
    if enc:
        from config import FERNET
        try:
            key = FERNET.decrypt(enc.encode()).decode()
        except Exception:
            raise AIError(t("ai_key_needs_reentry"), status=400)
    return base_url, model, key


def chat(messages):
    """Run one chat completion over the conversation in `messages`
    ([{role: 'user'|'assistant', content}]) and return the reply text."""
    base_url, model, key = _config()

    clean = []
    for m in messages[-MAX_MESSAGES:]:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        content = str(m.get("content") or "")[:MAX_MESSAGE_CHARS]
        if role in ("user", "assistant") and content:
            clean.append({"role": role, "content": content})

    system = (
        "You are the assistant built into One OS, a single-user server "
        "operations workbench (servers, terminals, file transfers, quick "
        "actions). Answer briefly and practically. When asked about the "
        "user's work or recent activity, rely on the operation history "
        "below (times are UTC).\n\n"
        "Operation history for the last %d days:\n%s"
        % (HISTORY_DAYS, history.digest(days=HISTORY_DAYS))
    )
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}] + clean,
    }

    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        raise AIError(t("ai_upstream_error",
                        msg=("HTTP %d %s" % (exc.code, detail)).strip()))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise AIError(t("ai_connection_failed", msg=str(exc)[:300]))

    try:
        content = body["choices"][0]["message"]["content"]
        return content if isinstance(content, str) else json.dumps(content)
    except (KeyError, IndexError, TypeError):
        raise AIError(t("ai_upstream_error", msg="unexpected response shape"))
