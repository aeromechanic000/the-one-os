# PLAN.md — Implementation Plan

Execution plan for "The One" Web OS (spec: `IDEA.md`). Phases mirror IDEA.md §10. Update the status column and progress log as work completes. After each phase: commit and push to `main` (see CLAUDE.md).

| Phase | Scope | Status |
|---|---|---|
| 1 | Skeleton + auth + i18n | ✅ |
| 2 | Responsive desktop shell + host management | ✅ |
| 3 | SSH terminal connectivity | ✅ |
| 4 | Virtual keyboard | ✅ |
| 5 | Notepad + cross-terminal paste | ✅ |
| 6 | Online monitoring + status lights | ✅ |
| 7 | Parameterized quick actions | ✅ |
| 8 | File transfer + relay | ✅ |
| 9 | File browser + preview | ✅ |
| 10 | Waiting-input detection | ✅ |
| W | Wrap-up: polish, smoke test, docs | ✅ |

Verification: `scripts/smoke.sh` (auth gate, login, all REST endpoints, host
CRUD, rule test, traversal rejection), `scripts/socket_smoke.py` (auth-gated
WebSocket, ack round-trips), `scripts/e2e_terminal_test.py` (live terminal
over an in-process SSH server: prompt, echo, sudo→waiting-input badge→cleared),
`scripts/e2e_files_test.py` (SFTP listing/preview/download/upload/ops,
remote→local transfer, path-traversal rejection). Run book: README §Testing.

## Contracts (keep frontend/backend in sync)

### REST API (all require login except `/auth/login`)

```
GET/POST /auth/login            POST {password}; GET /auth/logout
                                (login makes this frontend the active one)
GET     /api/session            → {login_at, expires_at, remaining_seconds}
GET     /api/terminals          → [{sid, host_id, name, state, attention}]
GET     /api/activation         → {client_id, active, your_ip, active_address,
                                  activated_at}   (single-active-frontend state)
POST    /api/activation/wake    → same; this frontend takes over as active
GET/POST/PUT/DELETE /api/hosts[/<id>]   GET → includes `online` + `local` flags
                                (`local`: entry points at this machine —
                                loopback/interface address/hostname/public IP;
                                recomputed at boot, on change, and hourly)
GET/POST/PUT/DELETE /api/actions[/<id>]
POST    /api/actions/<id>/run   {params} → {output, exit_status} | 400 {error, field}
GET     /api/transfer/files     → staged files in storage/
POST    /api/transfer/run       {src:{kind,host_id,path}, dst:{...}, mode:A|B|C}
GET     /api/transfer/queue     → [{id,state,percent,...}]
GET     /api/files/local?path=
GET     /api/files/remote?host_id=&path=
POST    /api/files/op           {op:mkdir|rename|delete, target:local|remote, host_id, path, new_path}
POST    /api/files/upload       multipart {target, host_id, path, file}
GET     /api/files/download     ?target=local|remote&host_id=&path=
GET     /api/preview            ?source=local|remote&host_id=&path=
GET/POST /api/attention/rules   CRUD; POST /api/attention/rules/test {rule, sample}
GET     /api/attention/rules/export   POST /api/attention/rules/import
GET/PUT /api/settings           key–value store (incl. ai_base_url, ai_model,
                                ai_api_key [write-only], history_days,
                                max_single_file_mb, max_storage_mb)
POST    /api/ai/chat            {messages:[{role,content}]} → {reply} | 400/502
                                (OpenAI-compatible proxy; injects a 7-day
                                operation-history digest as context)
GET     /api/history            ?days=&limit= → [{ts,type,host,detail}]

Single-active frontend: the latest login or wake is the only active client
(memory-only state). Non-GET requests from other logged-in frontends get
409 {error}; their socket events that mutate state (terminal_create/input/
resize/close) are ignored, while reads and mirrors keep flowing so a
sleeping frontend can wake and re-sync.
```

### SocketIO events

Client→server: `terminal_create {host_id}` (ack `{sid}`), `terminal_input {sid,data}`, `terminal_resize {sid,cols,rows}`, `terminal_close {sid}`, `terminal_attach {sid}`, `latency_probe` (ack).
Server→client: `terminal_output {sid,data}`, `terminal_meta {sid,host_id,name,state}`, `terminal_replay {sid,data}`, `terminal_closed {sid,reason}`, `session_attention {sid,name,confidence,hint}`, `session_attention_cleared {sid}`, `host_status {host_id,online}`, `hosts_changed`, `transfer_progress {id,state,percent,name,error}`, `activation_changed {client_id,address,activated_at}` (broadcast on login/wake), `activation_state` (per-socket on connect; same shape as /api/activation).

### DB tables

`hosts`, `actions`, `staged_files`, `attention_rules` (IDEA.md §7.1), `settings(key,value)`, `op_history(ts,type,host,detail)` — operation log pruned hourly per the `history_days` setting; read by the AI assistant.

## Phase details

### Phase 1 — Skeleton and Auth
- `requirements.txt`, `config.py` (paths + per-boot secrets), `main.py` (argparse, startup banner + password, create dirs chmod 700).
- `app/__init__.py` factory, `app/db.py` (thread-safe sqlite), `app/i18n.py` (EN dict), `app/auth.py`, `app/routes.py` (login/logout, desktop render, /api/session).
- `templates/base.html` (CDN libs, I18N injection), `templates/login.html` (mobile-adapted, English copy incl. "Password is printed in the server console…").
- Verify: server starts, password printed, login on desktop+phone widths, restart invalidates session, all UI English.

### Phase 2 — Desktop shell + host management
- `layout.js`: three forms (≥1024 desktop, ≥768 tablet, <768 phone), orientation handling.
- `desktop.js`: window manager (create/drag/resize/minimize/maximize/z-order/taskbar/Start menu, geometry in localStorage), desktop host icons + context menus, blank-area menu, phone host-list page + bottom nav (Hosts/Terminal/Files/Settings) + full-screen windows + app switcher + swipe-back.
- `app/hosts.py` CRUD + Fernet-encrypted passwords; Host Manager window UI.
- Verify: three forms correct, rotation reflows, host CRUD works.

### Phase 3 — Terminal
- `app/ssh_manager.py` (channels `{sid}`, reader threads, pooled SFTP clients, 10-min reattach buffer), `app/sockets.py`.
- `terminal.js`: xterm per session, fit/resize, status bar (Connected/Connecting/Disconnected + latency), reattach replay, broadcast model (all tabs see all terminals).
- visualViewport handling so keyboard never covers the input line.
- Verify: interactive terminal on desktop + phone widths.

### Phase 4 — Virtual keyboard
- `keyboard.js`: Layer 1 toolbar (Esc Tab Ctrl Alt arrows | ~ / - _ : " '), Layer 2 full panel (rows + F1–F12 + combos), Layer 3 custom keys from settings; sticky modifiers w/ double-tap lock; Windows/Mac display modes; coexist (`inputmode` default) vs replace (`inputmode=none`); hardware-keyboard detection.
- Verify: phone can send Esc/Tab/Ctrl+C/arrows/|/~; Mac hints change; custom keys appear.

### Phase 5 — Notepad + paste
- `notepad.js`: localStorage store, drag-to-terminal (desktop/tablet), "Send to Terminal" target picker (phone), selection→store (+clipboard attempt), paste into terminal.
- Verify: drag + button both deliver text to a shell.

### Phase 6 — Monitoring
- `app/monitor.py`: 30 s TCP probe (host:port), in-memory state, `host_status` on change + `hosts_changed` triggers immediate probe. Status lights (green/red/grey), top bar "Online 3 / 5", phone cards sync.
- Verify: state flips within 30 s of a host going down.

### Phase 7 — Quick actions
- `app/actions.py`: CRUD, `arguments_json` schema (text/select/number + pattern whitelist/options), backend validation mirrors frontend, `exec_command` run, built-ins: Add User, Check Disk, Restart Service, Check Memory (+ network category).
- `actions.js`: category sidebar, dynamic form, Run + output, "Invalid parameter" errors.
- Verify: Add User succeeds; invalid params rejected server-side.

### Phase 8 — File transfer
- `app/files.py`: storage staging + `staged_files`, async queue worker, modes A (disk relay) / B (streaming direct) / C (source-side scp), `transfer_progress`, 24 h cleanup hook.
- `transfer.js`: split panes (mobile stacked), queue with Queued/Transferring %/Completed/Failed.
- Verify: pull → push between two hosts via storage; phone-usable UI.

### Phase 9 — File browser + preview
- `app/files_browser.py` (listing/ops/upload/download), `app/preview.py` (extension dispatch, streaming + HTTP Range video, 2 MB text truncation, LibreOffice→PDF sha256-cached in `cache/preview/`, remote cache `cache/remote/`, archive name listing, 415 fallback).
- `files.js` (breadcrumb, toolbar, context menu) + `preview.js` (img/video/audio, PDF.js, marked+DOMPurify, pre, office-pdf, archive table, unsupported card; mobile full-screen + back).
- Verify: remote image renders, video seeks, PDF renders, MD sanitized, text truncation prompt, unknown → "Preview not supported".

### Phase 10 — Waiting-input detection
- `app/attention.py` engine in reader thread (SessionState, rule filter, silence timers, emit/cleared), built-in rules (sudo password, y/n, passwd, Python REPL, post-Ctrl+C).
- `app/attention_rules.py`: validation per IDEA.md §7.4, import/export §7.6, sample test (isolated, timeout).
- `settings.js` rules UI (list, editor w/ live regex validation, test pane); badges: title bar orange (solid/semi by confidence), taskbar dot, top bar "N waiting for input", phone counter tappable.
- Verify: sudo/y/n → high, Python REPL → medium, custom "Which to like" rule tests OK, import/export round-trips.

### Wrap-up
- Window layout memory audit, mobile gestures polish, full English copy audit, security review against IDEA.md §11, `scripts/smoke.sh`, README deployment docs (gunicorn + HTTPS note), PLAN.md progress final update.

## Progress log

- 2026-10-21 — Plan created; execution begins.
- 2026-10-21 — Phases 1-10 + wrap-up implemented in one pass (backend modules
  per §6, responsive shell, all window types, vendored frontend libs).
  Test-driven hardening fixed: `main.py` socketio import binding, Flask
  template/static root, lazy SFTP generator closing its session before
  streaming (remote download), disk-relay destination for remote→local,
  `dict.get(type=)` misuse in `/api/files/op`, host-edit password clearing.
  All four test suites green. Committed and pushed.
