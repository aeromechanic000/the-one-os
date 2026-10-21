"""Attention rule storage, validation (IDEA.md §7), built-ins, import/export."""
import json
import threading

from app import db
from app.attention import SessionState, evaluate_rule
from config import MAX_RULES

_cache_lock = threading.Lock()
_rules_cache = None

MAX_PATTERN_ITEMS = 10
MAX_SEQUENCE_ITEMS = 6
FLAG_RE = None  # placeholder; regex flags validated by compile

BUILTIN_RULES = [
    {
        "name": "sudo password",
        "match_mode": "all",
        "patterns": [
            {"type": "contains_any", "values": ["[sudo] password for", "password for"]},
            {"type": "line_end_no_newline"},
        ],
        "silence_ms": 400,
        "confidence": "high",
        "priority": 900,
        "window_ms": 30000,
        "sample": "[sudo] password for nil: ",
        "description": "sudo is asking for the user password",
    },
    {
        "name": "y/n confirmation",
        "match_mode": "all",
        "patterns": [
            {"type": "contains_any",
             "values": ["(y/n)", "[y/n]", "[Y/n]", "yes/no", "Y/n?"]},
            {"type": "line_end_no_newline"},
        ],
        "silence_ms": 400,
        "confidence": "high",
        "priority": 850,
        "window_ms": 30000,
        "sample": "Do you want to continue? [y/N] ",
        "description": "Command waits for a yes/no answer",
    },
    {
        "name": "passwd interaction",
        "match_mode": "all",
        "patterns": [
            {"type": "contains_any",
             "values": ["Enter new UNIX password", "Retype new UNIX password",
                        "New password:", "Retype new password:",
                        "Current password:"]},
            {"type": "line_end_no_newline"},
        ],
        "silence_ms": 400,
        "confidence": "high",
        "priority": 840,
        "window_ms": 30000,
        "sample": "Enter new UNIX password: ",
        "description": "passwd is waiting for a password entry",
    },
    {
        "name": "Python REPL prompt",
        "match_mode": "any",
        "patterns": [
            {"type": "regex", "value": r"^>>> ?$", "flags": "m"},
            {"type": "regex", "value": r"^In \[\d+\]: ?$", "flags": "m"},
        ],
        "silence_ms": 600,
        "confidence": "medium",
        "priority": 500,
        "window_ms": 30000,
        "sample": ">>> ",
        "description": "Python / IPython interactive prompt",
    },
    {
        "name": "re-prompt after Ctrl+C",
        "match_mode": "all",
        "patterns": [
            {"type": "after_interrupt"},
            {"type": "regex", "value": r"[$#] ?$"},
            {"type": "line_end_no_newline"},
        ],
        "silence_ms": 500,
        "confidence": "medium",
        "priority": 400,
        "window_ms": 5000,
        "sample": "^C\nuser@host:~$ ",
        "description": "Shell re-prompted after an interrupt",
    },
]

# Keys a client may send for a rule (everything else is server-managed).
EDITABLE_FIELDS = (
    "name", "enabled", "match_mode", "patterns", "silence_ms",
    "confidence", "priority", "window_ms", "sample", "description",
)


class ValidationError(ValueError):
    pass


# --- validation ---------------------------------------------------------------

def _validate_patterns(raw, match_mode):
    """Validate the patterns JSON array (IDEA.md §7.3/§7.4). Returns list."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            raise ValidationError("patterns must be a JSON array")
    if not isinstance(raw, list) or not (1 <= len(raw) <= MAX_PATTERN_ITEMS):
        raise ValidationError("patterns must be an array of 1-10 items")

    from app.attention import compile_regex

    cleaned = []
    has_regex = has_contains = has_context = False
    for item in raw:
        if not isinstance(item, dict):
            raise ValidationError("each feature must be an object")
        ftype = item.get("type")
        if ftype == "regex":
            value = item.get("value", "")
            if not isinstance(value, str) or not (1 <= len(value) <= 512):
                raise ValidationError("regex value must be 1-512 chars")
            flags = item.get("flags", "")
            if flags not in ("", "i", "m", "im"):
                raise ValidationError("invalid regex flags")
            try:
                compile_regex(value, flags)
            except Exception as exc:
                raise ValidationError(f"invalid regex: {exc}")
            has_regex = True
            cleaned.append({"type": "regex", "value": value, "flags": flags})
        elif ftype == "contains_any":
            values = item.get("values")
            if not isinstance(values, list) or not (1 <= len(values) <= 20):
                raise ValidationError("contains_any needs 1-20 values")
            vals = []
            for v in values:
                if isinstance(v, str) and 1 <= len(v) <= 64 and v not in vals:
                    vals.append(v)
            if not vals:
                raise ValidationError("contains_any has no valid values")
            has_contains = True
            cleaned.append({"type": "contains_any", "values": vals})
        elif ftype in ("line_end_no_newline", "cursor_not_at_col0", "after_interrupt"):
            cleaned.append({"type": ftype})
            if ftype == "after_interrupt":
                has_context = True
        elif ftype == "command_context":
            value = item.get("value", "")
            if not isinstance(value, str) or not (1 <= len(value) <= 512):
                raise ValidationError("command_context value must be 1-512 chars")
            flags = item.get("flags", "")
            if flags not in ("", "i"):
                raise ValidationError("invalid command_context flags")
            try:
                compile_regex(value, flags)
            except Exception as exc:
                raise ValidationError(f"invalid command_context regex: {exc}")
            has_context = True
            cleaned.append({"type": "command_context", "value": value, "flags": flags})
        else:
            raise ValidationError(f"unknown feature type: {ftype}")

    if match_mode == "sequence":
        if has_context:
            raise ValidationError(
                "sequence mode cannot use context features")
        if not (has_regex and has_contains):
            raise ValidationError(
                "sequence mode needs at least one regex and one contains_any")
        if len(cleaned) > MAX_SEQUENCE_ITEMS:
            raise ValidationError("sequence mode allows at most 6 features")
    if match_mode == "all" and len(cleaned) > MAX_SEQUENCE_ITEMS:
        raise ValidationError("all mode allows at most 6 features")
    if has_context:
        pass  # window_ms >= 1000 checked below together with other ranges
    return cleaned, has_context


def validate_rule(data, rule_id=None):
    """Validate the editable fields of a rule. Returns a cleaned dict."""
    name = (data.get("name") or "").strip()
    if not (1 <= len(name) <= 64):
        raise ValidationError("name must be 1-64 chars")
    dup = db.query_one(
        "SELECT id FROM attention_rules WHERE name = ? AND id != ?",
        (name, rule_id if rule_id is not None else -1),
    )
    if dup:
        raise ValidationError("name must be unique")

    match_mode = data.get("match_mode", "any")
    if match_mode not in ("any", "all", "sequence"):
        raise ValidationError("invalid match_mode")
    confidence = data.get("confidence", "high")
    if confidence not in ("high", "medium", "low"):
        raise ValidationError("invalid confidence")

    def _int(field, lo, hi, default):
        try:
            v = int(data.get(field, default))
        except (TypeError, ValueError):
            raise ValidationError(f"{field} must be an integer")
        if not (lo <= v <= hi):
            raise ValidationError(f"{field} must be {lo}-{hi}")
        return v

    silence_ms = _int("silence_ms", 0, 5000, 400)
    priority = _int("priority", 0, 9999, 100)
    window_ms = _int("window_ms", 0, 600000, 30000)
    enabled = 1 if str(data.get("enabled", 1)) in ("1", 1, "true", True) else 0

    patterns, has_context = _validate_patterns(data.get("patterns", "[]"), match_mode)
    if has_context and window_ms < 1000:
        raise ValidationError("context features require window_ms >= 1000")

    total = db.query_one("SELECT COUNT(*) AS c FROM attention_rules")["c"]
    if rule_id is None and total >= MAX_RULES:
        raise ValidationError(f"too many rules (max {MAX_RULES})")

    return {
        "name": name,
        "enabled": enabled,
        "match_mode": match_mode,
        "patterns": json.dumps(patterns),
        "silence_ms": silence_ms,
        "confidence": confidence,
        "priority": priority,
        "window_ms": window_ms,
        "sample": str(data.get("sample", ""))[:4000],
        "description": str(data.get("description", ""))[:500],
    }


# --- prepared rules for the engine --------------------------------------------

def _prepare(row):
    """Parse the stored row into the runtime rule dict used by attention.py."""
    rule = dict(row)
    try:
        rule["_patterns"] = json.loads(row["patterns"])
    except (TypeError, ValueError):
        rule["_patterns"] = []
    return rule


def enabled_rules_cached():
    global _rules_cache
    with _cache_lock:
        if _rules_cache is None:
            rows = db.query(
                "SELECT * FROM attention_rules WHERE enabled = 1 "
                "ORDER BY priority DESC"
            )
            _rules_cache = [_prepare(r) for r in rows]
        return _rules_cache


def invalidate_cache():
    global _rules_cache
    with _cache_lock:
        _rules_cache = None


def bump_hits(rule_id):
    db.execute(
        "UPDATE attention_rules SET hits = hits + 1 WHERE id = ?", (rule_id,)
    )


# --- CRUD ------------------------------------------------------------------------

def list_rules():
    rows = db.query("SELECT * FROM attention_rules ORDER BY priority DESC, name")
    out = []
    for r in rows:
        r = dict(r)
        try:
            r["patterns"] = json.loads(r["patterns"])
        except (TypeError, ValueError):
            r["patterns"] = []
        out.append(r)
    return out


def create_rule(data):
    clean = validate_rule(data)
    rid = db.execute(
        "INSERT INTO attention_rules (name, enabled, match_mode, patterns, "
        "silence_ms, confidence, priority, window_ms, sample, description) "
        "VALUES (:name, :enabled, :match_mode, :patterns, :silence_ms, "
        ":confidence, :priority, :window_ms, :sample, :description)",
        clean,
    )
    invalidate_cache()
    return rid


def update_rule(rule_id, data):
    row = db.query_one("SELECT * FROM attention_rules WHERE id = ?", (rule_id,))
    if not row:
        raise ValidationError("rule not found")
    merged = {k: row[k] for k in EDITABLE_FIELDS}
    merged.update({k: data[k] for k in EDITABLE_FIELDS if k in data})
    clean = validate_rule(merged, rule_id=rule_id)
    db.execute(
        "UPDATE attention_rules SET name=:name, enabled=:enabled, "
        "match_mode=:match_mode, patterns=:patterns, silence_ms=:silence_ms, "
        "confidence=:confidence, priority=:priority, window_ms=:window_ms, "
        "sample=:sample, description=:description, "
        "updated_at=datetime('now') WHERE id=:id",
        {**clean, "id": rule_id},
    )
    invalidate_cache()


def delete_rule(rule_id):
    row = db.query_one("SELECT * FROM attention_rules WHERE id = ?", (rule_id,))
    if not row:
        raise ValidationError("rule not found")
    if row["builtin"]:
        raise ValidationError("builtin rules cannot be deleted, only disabled")
    db.execute("DELETE FROM attention_rules WHERE id = ?", (rule_id,))
    invalidate_cache()


# --- sample test (isolated, timeout — IDEA.md §7.4/§11) ---------------------------

def test_rule(rule, sample):
    import concurrent.futures

    clean = validate_rule({**rule, "sample": sample})
    prepared = _prepare({**clean, "id": 0})

    state = SessionState()
    state.feed_output(sample)

    def run():
        matched, hits = evaluate_rule(prepared, state)
        hint = None
        if matched:
            from app.attention import derive_hint
            hint = derive_hint(state)
        return matched, hits, hint

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(run)
        try:
            matched, hits, hint = fut.result(timeout=3)
        except concurrent.futures.TimeoutError:
            raise ValidationError("test timed out")
    return {"matched": matched, "hits": hits, "hint": hint,
            "confidence": clean["confidence"]}


# --- import/export (IDEA.md §7.6) -----------------------------------------------

EXPORT_FORMAT = "webos-attention-rules"


def export_rules():
    from datetime import datetime, timezone
    rules = []
    for r in list_rules():
        rules.append({
            "name": r["name"], "enabled": r["enabled"],
            "match_mode": r["match_mode"], "patterns": r["patterns"],
            "silence_ms": r["silence_ms"], "confidence": r["confidence"],
            "priority": r["priority"], "window_ms": r["window_ms"],
            "sample": r["sample"], "description": r["description"],
        })
    return {
        "format": EXPORT_FORMAT,
        "version": 1,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "rules": rules,
    }


def import_rules(payload):
    if not isinstance(payload, dict) or payload.get("format") != EXPORT_FORMAT:
        raise ValidationError("invalid import format")
    if payload.get("version") != 1:
        raise ValidationError("unsupported import version")
    rules = payload.get("rules")
    if not isinstance(rules, list):
        raise ValidationError("rules must be an array")
    imported, skipped = 0, 0
    for r in rules:
        try:
            data = {k: r[k] for k in EDITABLE_FIELDS if k in r}
            data["enabled"] = r.get("enabled", 1)
            create_rule(data)  # name conflict raises -> skip
            imported += 1
        except ValidationError:
            skipped += 1
    return {"imported": imported, "skipped": skipped}


# --- seed -----------------------------------------------------------------------

def seed_builtins():
    for rule in BUILTIN_RULES:
        exists = db.query_one(
            "SELECT id FROM attention_rules WHERE name = ?", (rule["name"],))
        if not exists:
            db.execute(
                "INSERT INTO attention_rules (name, enabled, builtin, match_mode, "
                "patterns, silence_ms, confidence, priority, window_ms, sample, "
                "description) VALUES (?, 1, 1, ?, ?, ?, ?, ?, ?, ?, ?)",
                (rule["name"], rule["match_mode"],
                 json.dumps(rule["patterns"]), rule["silence_ms"],
                 rule["confidence"], rule["priority"], rule["window_ms"],
                 rule["sample"], rule["description"]),
            )
    invalidate_cache()
