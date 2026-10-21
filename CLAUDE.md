# CLAUDE.md

Guidance for Claude Code working in this repository. The full design specification is `IDEA.md` — read it before making architectural decisions. `PLAN.md` tracks the implementation roadmap and progress.

## What this project is

"The One" Web OS: a single-user, desktop-style, cross-device, English-only server operations workbench. One Flask process serves a browser "OS" where each server is a desktop icon; features (terminal, notepad, file transfer, quick actions, host manager, settings, file preview) are windows. Phone/tablet/desktop are three responsive forms of the same system.

Hard constraints (from IDEA.md §1):

- **Single user.** No multi-tenancy, no registration, no permission system.
- **No persistent identity.** Password is random per boot (`secrets.token_urlsafe(12)`), printed to console, memory-only. Sessions ≤ 7 days; restart invalidates everything.
- **English-first UI.** Every user-facing string goes through `app/i18n.py` (`t()` in Jinja, `window.I18N` in JS). No runtime language switching. Never hardcode English copy in templates/JS — add a key to the dictionary.
- **Files viewable in-system** (preview for images/video/audio/PDF/text/Markdown/Office/archives).

## Commands

```bash
# Setup (first time)
Run and manage the environment with [uv](https://docs.astral.sh/uv/) — dependencies are declared in `pyproject.toml` and `uv run` auto-syncs them on first use.

```bash
# Run (development) — prints the one-time password to stdout
uv run main.py                        # http://127.0.0.1:11686 (default port)
uv run main.py --host 0.0.0.0         # allow access from all addresses
uv run main.py --host 0.0.0.0 --port 11686

# Production (single worker, threads; see README)
uv run gunicorn -w 1 -k gthread --threads 32 -b 0.0.0.0:11686 main:app

# Syntax check everything
uv run python -m compileall -q main.py app

# Quick smoke test (starts server, checks login flow + API, kills it)
bash scripts/smoke.sh
```

Python 3.11+ (developed on 3.14), Flask + Flask-SocketIO (`async_mode='threading'` + `simple-websocket` — do NOT use eventlet), Paramiko for SSH/SFTP, stdlib `sqlite3` for storage (`webos.db`), `cryptography` Fernet for host-password encryption (key is memory-only per boot).

## Git workflow — IMPORTANT

**When a job (a phase from PLAN.md, or any user-requested unit of work) is finished: always commit and push to `main` of the GitHub repo (`origin`: github.com/aeromechanic000/the-one-os).**

- Commit directly on `main` (this is a personal project; no PR flow).
- Never commit secrets, `webos.db`, `storage/`, `cache/`, `.venv/`, `.DS_Store` (all gitignored).
- Commit message format: `Phase N: <summary>` or `Fix: <summary>`, plus `Co-Authored-By: Claude Code <noreply@anthropic.com>`.

## Architecture map

Backend (single Flask process + background threads):

- `main.py` — entry: argparse (`--host/--port/--debug`), prints password, starts SocketIO.
- `config.py` — paths, per-boot runtime secrets (password, SECRET_KEY, BOOT_ID), Fernet key.
- `app/__init__.py` — `create_app()` factory, DB init, background threads (monitor, cleanup).
- `app/db.py` — sqlite helpers (thread-safe), schema init.
- `app/i18n.py` — the English copy dictionary `EN` + `t(key, **kwargs)`.
- `app/auth.py` — password check, `login_required`, `before_request` double validation (BOOT_ID + 7-day expiry).
- `app/ssh_manager.py` — terminal channels (`{sid: TerminalSession}`), reader threads, pooled SSH/SFTP clients per host.
- `app/sockets.py` — SocketIO events (`terminal_create/input/resize/close`, latency probe).
- `app/hosts.py`, `app/actions.py`, `app/attention_rules.py` — REST CRUD for their tables.
- `app/attention.py` — waiting-input heuristic engine (runs inside reader threads).
- `app/monitor.py` — 30 s TCP probe thread → `host_status` push.
- `app/cleanup.py` — hourly: storage files >24 h, preview cache >24 h.
- `app/files.py` — transfer staging + 3 modes (A disk-relay, B streaming, C source-side scp), queue + progress.
- `app/files_browser.py` — local/remote directory listing + file ops.
- `app/preview.py` — `/api/preview` dispatch by extension; LibreOffice→PDF with sha256 cache.
- `app/routes.py` — all HTTP routes/blueprints.

Frontend (vanilla JS, no build step):

- `templates/` — `base.html`, `login.html`, `desktop.html`; `templates/windows/*.html` are window fragments included as `<template>` elements and cloned per window by JS.
- `static/js/` — `layout.js` (form detection), `desktop.js` (window manager, icons, taskbar, host manager UI), `terminal.js`, `keyboard.js`, `notepad.js`, `files.js`, `transfer.js`, `preview.js`, `actions.js`, `settings.js`.
- `static/css/` — `main.css` (desktop), `mobile.css` (phone/tablet), `keyboard.css`.
- Vendored from CDN in `base.html`: socket.io, xterm.js + fit addon, PDF.js, marked, DOMPurify.

SocketIO event names and REST API contracts are listed in PLAN.md §Contracts — keep frontend and backend in sync when changing them.

## Conventions & security boundaries

- Background threads never touch Flask request context; push via `socketio.emit` from the `app/sockets.py` socketio instance.
- SQLite access goes through `app/db.py` (single connection + lock; `check_same_thread=False`).
- Preview/file endpoints: require login, normalize paths, forbid `..` traversal; local paths must stay under `storage/`; remote paths only via an authenticated host connection. SVG renders via `<img>` (never inline), Markdown is DOMPurify-sanitized on the frontend, archives list names only (never extract), LibreOffice runs in an isolated temp dir with a 30 s timeout.
- Quick-action parameters: strict whitelist (regex `pattern` or `options` enum) on BOTH frontend and backend; templates only get validated placeholder values.
- Attention rules: regex compiled with LRU cache, input truncated to last 2048 chars, ≤200 rules, builtin rules cannot be deleted (only disabled/copied), sample test runs with a timeout.
- Window geometry per window type persists in `localStorage`; notepad content persists in `localStorage`.
- Visual language: bg `#1e1e2e`, window `#2a2a3c`, accent `#4fc3f7`, attention orange `#ff9800`, radius 8px, transitions 150–200ms; touch targets ≥44×44px; terminal font "JetBrains Mono", UI font Inter/Segoe UI stack. Title Case for titles/buttons, Sentence case for status text.
- Time format `YYYY-MM-DD HH:mm:ss`; relative time in English ("3 minutes ago").
