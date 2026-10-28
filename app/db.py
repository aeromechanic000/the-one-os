"""Thread-safe SQLite access (single connection + RLock).

The whole app is one process with background threads (monitor, cleanup,
transfer worker, SSH reader threads), so a module-level connection guarded by
a lock is the simplest correct approach at this scale.
"""
import sqlite3
import threading

from config import DB_PATH

_lock = threading.RLock()
_conn = None

SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS hosts (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        name               TEXT NOT NULL UNIQUE,
        host               TEXT NOT NULL,
        port               INTEGER NOT NULL DEFAULT 22,
        username           TEXT NOT NULL DEFAULT 'root',
        auth_method        TEXT NOT NULL DEFAULT 'password'
                           CHECK (auth_method IN ('password', 'key')),
        key_path           TEXT DEFAULT '',
        password_encrypted TEXT DEFAULT '',
        group_name         TEXT DEFAULT '',
        is_local           INTEGER NOT NULL DEFAULT 0
                           CHECK (is_local IN (0, 1)),
        created_at         TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at         TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS actions (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        name             TEXT NOT NULL UNIQUE,
        category         TEXT NOT NULL DEFAULT 'System',
        description      TEXT DEFAULT '',
        command_template TEXT NOT NULL,
        arguments_json   TEXT NOT NULL DEFAULT '[]',
        builtin          INTEGER NOT NULL DEFAULT 0 CHECK (builtin IN (0, 1)),
        created_at       TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at       TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS staged_files (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        filename     TEXT NOT NULL,
        stored_path  TEXT NOT NULL,
        size         INTEGER NOT NULL DEFAULT 0,
        sha256       TEXT DEFAULT '',
        source_host  TEXT DEFAULT '',
        created_at   TEXT NOT NULL DEFAULT (datetime('now')),
        expires_at   TEXT NOT NULL DEFAULT (datetime('now', '+1 day'))
    )
    """,
    # attention_rules: exact structure from IDEA.md §7.1
    """
    CREATE TABLE IF NOT EXISTS attention_rules (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        name        TEXT NOT NULL UNIQUE,
        enabled     INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
        builtin     INTEGER NOT NULL DEFAULT 0 CHECK (builtin IN (0, 1)),
        match_mode  TEXT NOT NULL DEFAULT 'any'
                    CHECK (match_mode IN ('any', 'all', 'sequence')),
        patterns    TEXT NOT NULL,
        silence_ms  INTEGER NOT NULL DEFAULT 400
                    CHECK (silence_ms BETWEEN 0 AND 5000),
        confidence  TEXT NOT NULL DEFAULT 'high'
                    CHECK (confidence IN ('high', 'medium', 'low')),
        priority    INTEGER NOT NULL DEFAULT 100
                    CHECK (priority BETWEEN 0 AND 9999),
        window_ms   INTEGER NOT NULL DEFAULT 30000
                    CHECK (window_ms BETWEEN 0 AND 600000),
        sample      TEXT DEFAULT '',
        description TEXT DEFAULT '',
        hits        INTEGER NOT NULL DEFAULT 0,
        created_at  TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_attention_enabled_priority "
    "ON attention_rules(enabled, priority DESC)",
    """
    CREATE TABLE IF NOT EXISTS settings (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL DEFAULT ''
    )
    """,
    # op_history: append-only operation log; the AI assistant reads a digest
    # of recent rows, cleanup.py prunes by the history_days setting.
    """
    CREATE TABLE IF NOT EXISTS op_history (
        id     INTEGER PRIMARY KEY AUTOINCREMENT,
        ts     TEXT NOT NULL DEFAULT (datetime('now')),
        type   TEXT NOT NULL,
        host   TEXT NOT NULL DEFAULT '',
        detail TEXT NOT NULL DEFAULT ''
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_op_history_ts ON op_history(ts)",
    # ai_tokens: LLM provider tokens for the usage monitor (app/ai_usage.py).
    # Config (base_url/auth_style/usage_url/extract_path) is prefilled from
    # a provider template but fully editable per token. token_enc uses the
    # persisted AI_TOKEN_FERNET key (survives restarts); last4 is kept in
    # the clear for masked display.
    """
    CREATE TABLE IF NOT EXISTS ai_tokens (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        provider      TEXT NOT NULL DEFAULT 'custom',
        label         TEXT NOT NULL DEFAULT '',
        base_url      TEXT NOT NULL DEFAULT '',
        auth_style    TEXT NOT NULL DEFAULT 'bearer'
                      CHECK (auth_style IN
                             ('bearer', 'x-api-key', 'raw', 'oauth')),
        usage_url     TEXT NOT NULL DEFAULT '',
        extract_path  TEXT NOT NULL DEFAULT '',
        token_enc     TEXT NOT NULL,
        last4         TEXT NOT NULL DEFAULT '',
        created_at    TEXT NOT NULL DEFAULT (datetime('now')),
        last_check_at TEXT NOT NULL DEFAULT '',
        last_check    TEXT NOT NULL DEFAULT ''
    )
    """,
]


def get_conn():
    global _conn
    with _lock:
        if _conn is None:
            _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
        return _conn


def init_db():
    with _lock:
        conn = get_conn()
        for stmt in SCHEMA:
            conn.execute(stmt)
        # lightweight one-time migrations (no framework — keep explicit)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(hosts)")}
        if "is_local" not in cols:
            conn.execute("ALTER TABLE hosts "
                         "ADD COLUMN is_local INTEGER NOT NULL DEFAULT 0")
        tok_cols = {r[1] for r in conn.execute("PRAGMA table_info(ai_tokens)")}
        for col, ddl in (
            ("base_url", "TEXT NOT NULL DEFAULT ''"),
            ("auth_style", "TEXT NOT NULL DEFAULT 'bearer'"),
            ("usage_url", "TEXT NOT NULL DEFAULT ''"),
            ("extract_path", "TEXT NOT NULL DEFAULT ''"),
        ):
            if col not in tok_cols:
                conn.execute("ALTER TABLE ai_tokens ADD COLUMN %s %s"
                             % (col, ddl))
        # auth_style later gained 'raw' (Zhipu) and 'oauth' (Claude
        # subscriptions). SQLite cannot ALTER a CHECK constraint, so when
        # the old two-style constraint is detected the table is rebuilt
        # from the current schema, preserving all rows.
        row = conn.execute("SELECT sql FROM sqlite_master "
                           "WHERE type='table' AND name='ai_tokens'").fetchone()
        if row and "'x-api-key')" in (row[0] or ""):
            create = next(s for s in SCHEMA if "ai_tokens" in s)
            conn.execute("ALTER TABLE ai_tokens RENAME TO ai_tokens_old")
            conn.execute(create)
            conn.execute("INSERT INTO ai_tokens SELECT * FROM ai_tokens_old")
            conn.execute("DROP TABLE ai_tokens_old")
        conn.commit()


def query(sql, params=()):
    """Fetch all rows as a list of dicts."""
    with _lock:
        cur = get_conn().execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def query_one(sql, params=()):
    with _lock:
        cur = get_conn().execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row else None


def execute(sql, params=()):
    """Run a mutating statement; returns lastrowid."""
    with _lock:
        conn = get_conn()
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid


def get_setting(key, default=""):
    row = query_one("SELECT value FROM settings WHERE key = ?", (key,))
    return row["value"] if row else default


def set_setting(key, value):
    execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )
