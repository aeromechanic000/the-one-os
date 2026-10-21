# The One — Web OS

A single-user, desktop-style, cross-device, English-first server operations
workbench. Open it from any browser — desktop, tablet, or phone — and get a
desktop-OS-like interface where every server is an icon: click to open a
terminal, drag/resize windows, switch via the taskbar, watch status lights,
transfer files between machines, preview files in-system, and get a badge when
a session is waiting for input (sudo password, y/n, …). Full design spec:
[IDEA.md](IDEA.md); development guide: [CLAUDE.md](CLAUDE.md); roadmap:
[PLAN.md](PLAN.md).

## Features

- **SSH terminal** (xterm.js) with reattach, latency display, and per-window
  status bar; sessions survive tab switches and reconnects for 10 minutes.
- **Waiting-input detection** — heuristic rule engine (prompt pattern +
  output silence + command context + interrupt marker) with built-in rules
  for sudo passwords, y/n confirmations, passwd, Python REPL, and
  post-Ctrl+C re-prompts. Rules are CRUD-able, sample-testable, and
  importable/exportable. Hint only — never auto-replies.
- **Virtual keyboard** for phones — layered keys (Esc/Tab/Ctrl/arrows/symbols,
  full panel with F-keys and combos, custom keys), sticky modifiers with
  double-tap lock, Windows/Mac display modes, coexist/replace strategies,
  hardware-keyboard detection.
- **Notepad** with cross-terminal paste: drag to a terminal (desktop) or
  "Send to Terminal" (phone); terminal selections feed the notepad.
- **File browser + transfer** over Storage (staging) and remote hosts:
  upload/download, rename/delete/mkdir, three transfer modes (disk relay,
  direct stream, source-side scp) with live progress.
- **In-system preview**: images, video (HTTP Range seeking), audio, PDF
  (PDF.js), Markdown (marked + DOMPurify), text/code (2 MB truncation
  notice), archives (entry names), and Office documents via server-side
  LibreOffice → PDF conversion (SHA-256 cached), with simple-frontend and
  disable fallbacks.
- **Parameterized quick actions** with strict client+server validation
  (Add User, Check Disk, Restart Service, Check Memory, ports, ping…).
- **Online monitoring** — 30 s TCP probes, status lights, top-bar counter.
- **No persistent identity** — the login password is random per boot and
  printed to the console; sessions last at most 7 days; restarting
  invalidates everything. Host passwords are encrypted with a per-boot key
  (re-enter them after a restart).

## Quickstart

Requires [uv](https://docs.astral.sh/uv/) and Python 3.11+.

```bash
uv run main.py                 # http://127.0.0.1:11686
```

The startup banner prints the one-time password. Sign in, add a host in
**Host Manager**, and click its desktop icon to open a terminal.

Useful flags:

```bash
uv run main.py --host 0.0.0.0            # allow access from all addresses
uv run main.py --host 0.0.0.0 --port 11686
uv run main.py --debug                   # development auto-reload
```

## Production

```bash
uv run gunicorn -w 1 -k gthread --threads 32 -b 0.0.0.0:11686 main:app
```

- Single worker only: connections, terminals, and the transfer queue live in
  process memory.
- Put TLS in front (nginx/Caddy) — cookies and terminal streams should not
  travel plain HTTP. The app itself is HTTP.
- Optional: install LibreOffice for high-fidelity Office preview (override
  the binary with `WEBOS_SOFFICE=/path/to/soffice`). Without it, Office
  preview falls back to the "simple frontend render" or "disabled" settings.

## Testing

```bash
bash scripts/smoke.sh                                   # API + auth + login flow
uv run main.py --port 11687 &                           # then:
uv run python scripts/socket_smoke.py <url> <password>  # SocketIO layer
uv run python scripts/e2e_terminal_test.py <url> <pw>   # terminal + attention
uv run python scripts/e2e_files_test.py <url> <pw>      # SFTP + preview + transfer
```

The E2E scripts spin up their own in-process fake SSH/SFTP servers — they
never touch your real machines.

## Layout

```
main.py            entry point (argparse, banner, SocketIO)
app/               Flask app: auth, i18n, ssh_manager, sockets, hosts,
                   actions, files, files_browser, preview, monitor,
                   cleanup, attention(_rules), routes, db
templates/         login + desktop shell + window fragments
static/            vanilla JS modules + CSS + vendored libraries
storage/           transfer staging (gitignored, 24 h TTL)
cache/             preview/remote caches (gitignored, 24 h TTL)
webos.db           SQLite (gitignored)
```
