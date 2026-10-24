"""Operation history: an append-only log of significant user operations
(actions run, transfers, uploads/downloads, file ops, host changes, logins,
settings updates, AI chats).

The AI assistant reads a compact digest of recent entries as context, so the
user can ask things like "summarize my work in the last 7 days". Entries are
pruned hourly by cleanup.py according to the history_days setting.
"""
import time

from app import db

MAX_DETAIL = 300  # chars per entry detail

# Entry types: login | settings_update | host_add | host_update | host_delete |
#              action_run | transfer | upload | download | file_op | preview |
#              ai_chat


def record(type_, host="", detail=""):
    """Append one entry. Never raises — history must not break the operation
    it records."""
    try:
        db.execute(
            "INSERT INTO op_history (type, host, detail) VALUES (?, ?, ?)",
            (str(type_)[:32], str(host or "")[:80],
             str(detail or "")[:MAX_DETAIL]),
        )
    except Exception:
        pass


def _cutoff(days):
    """UTC timestamp `days` days ago, in SQLite datetime('now') format."""
    return time.strftime(
        "%Y-%m-%d %H:%M:%S", time.gmtime(time.time() - max(1, days) * 86400))


def list_history(days=7, limit=1000):
    """Newest-first entries from the last `days` days."""
    try:
        days = max(1, int(days))
        limit = max(1, min(int(limit), 5000))
    except (TypeError, ValueError):
        days, limit = 7, 1000
    return db.query(
        "SELECT ts, type, host, detail FROM op_history WHERE ts >= ? "
        "ORDER BY id DESC LIMIT ?",
        (_cutoff(days), limit),
    )


def digest(days=7, max_chars=6000):
    """Compact newest-first text digest of recent operations, for the AI."""
    rows = list_history(days=days, limit=800)
    if not rows:
        return "No recorded operations in the last %d days." % days
    counts = {}
    for r in rows:
        counts[r["type"]] = counts.get(r["type"], 0) + 1
    lines = [", ".join("%s x%d" % (k, v) for k, v in sorted(counts.items()))]
    used = len(lines[0])
    for r in rows:
        line = "- [%s] %s" % (r["ts"], r["type"])
        if r["host"]:
            line += " @%s" % r["host"]
        if r["detail"]:
            line += ": %s" % r["detail"]
        if used + len(line) > max_chars:
            lines.append("... (older entries omitted)")
            break
        lines.append(line)
        used += len(line)
    return "\n".join(lines)


def prune(days):
    """Delete entries older than `days` days (called hourly by cleanup)."""
    db.execute("DELETE FROM op_history WHERE ts < ?", (_cutoff(days),))


def host_label(host_id):
    """Host name for history entries ('' for unknown/local)."""
    if not host_id:
        return ""
    row = db.query_one("SELECT name FROM hosts WHERE id = ?", (host_id,))
    return row["name"] if row else str(host_id)
