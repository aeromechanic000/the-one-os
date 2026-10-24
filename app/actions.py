"""Parameterized quick actions (IDEA.md §6.6).

Command templates contain only predefined placeholders; every argument is
validated against a strict whitelist (regex pattern / option list / numeric
range) on the backend before the template is rendered — the frontend applies
the same rules for instant feedback.
"""
import json
import re

from flask import Blueprint, jsonify, request

from app import db
from app.i18n import t
from app.ssh_manager import SSHError, manager

bp = Blueprint("actions", __name__)

NAME_RE = re.compile(r"^[a-zA-Z0-9_ -]{1,64}$")
PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
RUN_TIMEOUT = 30

BUILTIN_ACTIONS = [
    {
        "name": "Add User",
        "category": "User Management",
        "description": "Create a new user account",
        "command_template": "useradd -m {username}",
        "arguments": [
            {"name": "username", "label": "Username", "type": "text",
             "required": True,
             "pattern": "^[a-zA-Z_][a-zA-Z0-9_-]{0,31}$"},
        ],
    },
    {
        "name": "Restart Service",
        "category": "Service Management",
        "description": "systemctl restart <service>",
        "command_template": "systemctl restart {service}",
        "arguments": [
            {"name": "service", "label": "Service", "type": "text",
             "required": True,
             "pattern": "^[a-zA-Z0-9_.@-]{1,64}$"},
        ],
    },
    {
        "name": "Service Status",
        "category": "Service Management",
        "description": "systemctl status <service>",
        "command_template": "systemctl status {service} --no-pager",
        "arguments": [
            {"name": "service", "label": "Service", "type": "text",
             "required": True,
             "pattern": "^[a-zA-Z0-9_.@-]{1,64}$"},
        ],
    },
    {
        "name": "Check Disk",
        "category": "Disk",
        "description": "df -h <path>",
        "command_template": "df -h {path}",
        "arguments": [
            {"name": "path", "label": "Path", "type": "text", "required": True,
             "pattern": "^/[a-zA-Z0-9._/-]{0,120}$", "default": "/"},
        ],
    },
    {
        "name": "Check Memory",
        "category": "Disk",
        "description": "free -h",
        "command_template": "free -h",
        "arguments": [],
    },
    {
        "name": "Check Ports",
        "category": "Network",
        "description": "Listening sockets (ss)",
        "command_template": "ss -tulpn | head -n 60",
        "arguments": [],
    },
    {
        "name": "Ping Host",
        "category": "Network",
        "description": "ping a host 4 times from the server",
        "command_template": "ping -c 4 -W 2 {target}",
        "arguments": [
            {"name": "target", "label": "Target", "type": "text",
             "required": True,
             "pattern": "^[a-zA-Z0-9.:_-]{1,128}$"},
        ],
    },
]


def _parse_args(row):
    try:
        args = json.loads(row["arguments_json"])
    except (TypeError, ValueError):
        args = []
    row["arguments"] = args if isinstance(args, list) else []
    return row


def validate_params(spec, params):
    """Validate user params against an argument spec list.

    Returns (clean: dict, error: None | {'field', 'error'}).
    """
    clean = {}
    declared = set()
    for arg in spec:
        name = arg.get("name", "")
        declared.add(name)
        value = params.get(name, arg.get("default", ""))
        value = str(value if value is not None else "").strip()
        if arg.get("type") == "number":
            try:
                num = int(value)
            except ValueError:
                return None, {"field": name, "error": t("qa_invalid")}
            lo, hi = arg.get("min"), arg.get("max")
            if lo is not None and num < lo or hi is not None and num > hi:
                return None, {"field": name, "error": t("qa_invalid")}
            value = str(num)
        elif arg.get("type") == "select":
            options = [str(o) for o in arg.get("options", [])]
            if value not in options:
                return None, {"field": name, "error": t("qa_invalid")}
        else:  # text
            pattern = arg.get("pattern")
            if pattern:
                try:
                    if not re.fullmatch(pattern, value):
                        return None, {"field": name, "error": t("qa_invalid")}
                except re.error:
                    return None, {"field": name, "error": t("qa_invalid")}
            elif len(value) > 256:
                return None, {"field": name, "error": t("qa_invalid")}
        if arg.get("required") and not value:
            return None, {"field": name, "error": t("qa_invalid")}
        clean[name] = value
    extra = set(params.keys()) - declared
    if extra:
        return None, {"field": sorted(extra)[0], "error": t("qa_invalid")}
    return clean, None


def _validate_action(data, action_id=None):
    name = (data.get("name") or "").strip()
    if not NAME_RE.fullmatch(name):
        raise ValueError("name: 1-64 chars, letters/digits/_/-")
    template = (data.get("command_template") or "").strip()
    if not template or len(template) > 2000:
        raise ValueError("command_template: 1-2000 chars")
    dup = db.query_one(
        "SELECT id FROM actions WHERE name = ? AND id != ?",
        (name, action_id if action_id is not None else -1))
    if dup:
        raise ValueError("duplicate name")
    args = data.get("arguments", [])
    if not isinstance(args, list) or len(args) > 20:
        raise ValueError("arguments: list of at most 20")
    placeholders = set(PLACEHOLDER_RE.findall(template))
    names = set()
    for a in args:
        if not isinstance(a, dict) or not a.get("name"):
            raise ValueError("argument needs a name")
        if a["name"] in names:
            raise ValueError("duplicate argument name")
        names.add(a["name"])
        if a.get("type") not in ("text", "select", "number"):
            a["type"] = "text"
        if a.get("pattern"):
            try:
                re.compile(a["pattern"])
            except re.error:
                raise ValueError(f"invalid pattern for {a['name']}")
    unknown = placeholders - names
    if unknown:
        raise ValueError(f"undeclared placeholders: {sorted(unknown)}")
    return {
        "name": name,
        "category": (data.get("category") or "System").strip()[:64] or "System",
        "description": (data.get("description") or "").strip()[:500],
        "command_template": template,
        "arguments_json": json.dumps(args),
    }


@bp.get("/api/actions")
def list_actions():
    rows = db.query("SELECT * FROM actions ORDER BY category, name")
    return jsonify([_parse_args(r) for r in rows])


@bp.post("/api/actions")
def create_action():
    data = request.get_json(silent=True) or {}
    try:
        clean = _validate_action(data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    rid = db.execute(
        "INSERT INTO actions (name, category, description, command_template, "
        "arguments_json) VALUES (:name, :category, :description, "
        ":command_template, :arguments_json)", clean)
    return jsonify({"id": rid}), 201


@bp.put("/api/actions/<int:action_id>")
def update_action(action_id):
    row = db.query_one("SELECT * FROM actions WHERE id = ?", (action_id,))
    if not row:
        return jsonify({"error": t("not_found")}), 404
    data = request.get_json(silent=True) or {}
    try:
        clean = _validate_action(data, action_id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    db.execute(
        "UPDATE actions SET name=:name, category=:category, "
        "description=:description, command_template=:command_template, "
        "arguments_json=:arguments_json, updated_at=datetime('now') "
        "WHERE id=:id", {**clean, "id": action_id})
    return jsonify({"ok": True})


@bp.delete("/api/actions/<int:action_id>")
def delete_action(action_id):
    row = db.query_one("SELECT * FROM actions WHERE id = ?", (action_id,))
    if not row:
        return jsonify({"error": t("not_found")}), 404
    if row["builtin"]:
        return jsonify({"error": "built-in action cannot be deleted"}), 400
    db.execute("DELETE FROM actions WHERE id = ?", (action_id,))
    return jsonify({"ok": True})


@bp.post("/api/actions/<int:action_id>/run")
def run_action(action_id):
    row = db.query_one("SELECT * FROM actions WHERE id = ?", (action_id,))
    if not row:
        return jsonify({"error": t("not_found")}), 404
    data = request.get_json(silent=True) or {}
    host_id = data.get("host_id")
    params = data.get("params") or {}
    if not host_id:
        return jsonify({"error": "host_id is required"}), 400

    row = _parse_args(row)
    clean, err = validate_params(row["arguments"], params)
    if err:
        return jsonify({"error": err["error"], "field": err["field"]}), 400
    try:
        command = row["command_template"].format(**clean)
    except (KeyError, IndexError, ValueError):
        return jsonify({"error": "template error"}), 400

    try:
        output, exit_status = manager.run_exec(host_id, command,
                                               timeout=RUN_TIMEOUT)
    except SSHError as exc:
        return jsonify({"error": str(exc)}), 502
    from app import history
    history.record(
        "action_run", history.host_label(host_id),
        "ran '%s' -> exit %d" % (row["name"], exit_status))
    return jsonify({"command": command, "output": output,
                    "exit_status": exit_status})


def seed_builtins():
    for action in BUILTIN_ACTIONS:
        exists = db.query_one(
            "SELECT id FROM actions WHERE name = ?", (action["name"],))
        if exists:
            continue
        db.execute(
            "INSERT INTO actions (name, category, description, "
            "command_template, arguments_json, builtin) "
            "VALUES (?, ?, ?, ?, ?, 1)",
            (action["name"], action["category"], action["description"],
             action["command_template"], json.dumps(action["arguments"])),
        )
