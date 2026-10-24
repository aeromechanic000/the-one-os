"""Host management REST API (IDEA.md §6.9).

Passwords are Fernet-encrypted with a per-boot in-memory key: after a service
restart they cannot be decrypted and the UI asks for re-entry.
"""
from flask import Blueprint, jsonify, request

from app import db, history, monitor
from app.i18n import t
from app.ssh_manager import manager
from config import FERNET

bp = Blueprint("hosts", __name__)


def _public(row):
    needs_password = False
    if row.get("password_encrypted"):
        try:
            FERNET.decrypt(row["password_encrypted"].encode())
        except Exception:
            needs_password = True
    return {
        "id": row["id"],
        "name": row["name"],
        "host": row["host"],
        "port": row["port"],
        "username": row["username"],
        "auth_method": row["auth_method"],
        "key_path": row["key_path"] or "",
        "group_name": row["group_name"] or "",
        "needs_password": needs_password,
    }


def _validate(data, host_id=None):
    name = (data.get("name") or "").strip()
    host = (data.get("host") or "").strip()
    username = (data.get("username") or "root").strip()
    auth_method = data.get("auth_method") or "password"
    if not (1 <= len(name) <= 64):
        raise ValueError(t("f_name") + ": 1-64")
    if not host or len(host) > 255:
        raise ValueError(t("f_host") + ": 1-255")
    try:
        port = int(data.get("port") or 22)
        if not (1 <= port <= 65535):
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError(t("f_port") + ": 1-65535")
    if auth_method not in ("password", "key"):
        raise ValueError(t("f_auth"))
    dup = db.query_one(
        "SELECT id FROM hosts WHERE name = ? AND id != ?",
        (name, host_id if host_id is not None else -1))
    if dup:
        raise ValueError("duplicate name")
    key_path = (data.get("key_path") or "").strip()
    if auth_method == "key" and not key_path:
        raise ValueError(t("f_keyfile"))
    return {
        "name": name, "host": host, "port": port, "username": username,
        "auth_method": auth_method, "key_path": key_path,
        "group_name": (data.get("group_name") or "").strip(),
    }


@bp.get("/api/hosts")
def list_hosts():
    rows = db.query("SELECT * FROM hosts ORDER BY name")
    status = monitor.get_status()
    out = []
    for r in rows:
        item = _public(r)
        item["online"] = status.get(r["id"])  # True/False/None
        out.append(item)
    return jsonify(out)


@bp.post("/api/hosts")
def create_host():
    data = request.get_json(silent=True) or {}
    try:
        clean = _validate(data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    enc = ""
    password = data.get("password") or ""
    if password:
        enc = FERNET.encrypt(password.encode()).decode()
    rid = db.execute(
        "INSERT INTO hosts (name, host, port, username, auth_method, "
        "key_path, password_encrypted, group_name) "
        "VALUES (:name, :host, :port, :username, :auth_method, :key_path, "
        ":enc, :group_name)",
        {**clean, "enc": enc},
    )
    monitor.hosts_changed()
    history.record("host_add", clean["name"],
                   "added host %s (%s)" % (clean["name"], clean["host"]))
    return jsonify({"id": rid}), 201


@bp.put("/api/hosts/<int:host_id>")
def update_host(host_id):
    row = db.query_one("SELECT * FROM hosts WHERE id = ?", (host_id,))
    if not row:
        return jsonify({"error": t("not_found")}), 404
    data = request.get_json(silent=True) or {}
    try:
        clean = _validate(data, host_id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    # Keep the previous encrypted password unless a new one is provided.
    enc = row["password_encrypted"]
    password = data.get("password")
    if password:  # non-empty string replaces; empty string clears
        enc = FERNET.encrypt(password.encode()).decode()
    elif password == "":
        enc = ""
    db.execute(
        "UPDATE hosts SET name=:name, host=:host, port=:port, "
        "username=:username, auth_method=:auth_method, key_path=:key_path, "
        "password_encrypted=:enc, group_name=:group_name, "
        "updated_at=datetime('now') WHERE id=:id",
        {**clean, "enc": enc, "id": host_id},
    )
    manager.drop_host(host_id)  # pooled connection may be stale now
    monitor.drop_host(host_id)
    monitor.hosts_changed()
    history.record("host_update", clean["name"], "updated host %s" % clean["name"])
    return jsonify({"ok": True})


@bp.delete("/api/hosts/<int:host_id>")
def delete_host(host_id):
    row = db.query_one("SELECT * FROM hosts WHERE id = ?", (host_id,))
    if not row:
        return jsonify({"error": t("not_found")}), 404
    manager.drop_host(host_id)
    monitor.drop_host(host_id)
    db.execute("DELETE FROM hosts WHERE id = ?", (host_id,))
    monitor.hosts_changed()
    history.record("host_delete", row["name"], "deleted host %s" % row["name"])
    return jsonify({"ok": True})
