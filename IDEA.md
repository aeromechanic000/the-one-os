The "One" Web OS Software Design Specification

1. Design Origin: What It Is

This is not a "Web SSH tool." It is a single-user, desktop-style, cross-device, English-first server operations workbench. Users open it from anywhere via a browser—desktop, tablet, or phone—and see an interface resembling a desktop operating system: each server appears as an icon on the desktop, single-click opens a terminal; windows can be dragged, resized, and minimized; the taskbar switches windows; the top bar shows global status. It unifies SSH terminal, notepad, file transfer, parameterized quick actions, online monitoring, waiting-input detection, virtual keyboard, and file preview into one desktop paradigm, adapting automatically to screen orientation and size. All user-facing UI text is in English. The file browser supports opening common formats directly inside the system.

Five constraints define the design:

Constraint 1: Single user. No multi-tenancy, no permission system, no user registration. All design is optimized around "this one user's habits."

Constraint 2: No persistent identity. Password is randomly generated at each startup, printed to the console, held only in memory. Session lasts at most 7 days; restarting the program invalidates everything. The system stores no user credentials.

Constraint 3: Devices are objects, features are tools, screen is a variable. Servers are first-class citizens on the desktop; feature panels are windows serving server operations; layout adapts to screen size and orientation—desktop, tablet, phone landscape, phone portrait are four presentations of the same system.

Constraint 4: English-first. All user-facing copy, prompts, error messages, and settings are in English. Internal code, logs, and SocketIO event names remain English for maintainability; the user-visible layer is English as well.

Constraint 5: Files can be viewed in-system. Common files on the transfer storage and remote servers (images, video, audio, PDF, Office documents, Markdown, plain text) can be previewed directly inside Web OS without requiring the user to download them first.

2. Overall Architecture

The system is a three-layer structure: single-process Flask application + responsive frontend desktop shell + background daemon threads.

Backend (Flask + SocketIO + Paramiko) handles six responsibilities: authentication and session, SSH/SFTP connection management, background monitoring, waiting-input detection, configuration and metadata storage, file preview and format conversion. Flask renders the desktop shell and window fragments; SocketIO carries terminal bidirectional streams and status pushes; Paramiko performs all remote connections.

Frontend (responsive desktop shell + window manager + window components + virtual keyboard + file previewer) handles five responsibilities: window lifecycle management, internal interaction of each feature window, communication with the backend, layout and input switching per screen form, rendering previews by file type.

Background threads (monitor + attention + cleanup) run independently of request cycles: monitor probes SSH port reachability for all hosts every 30 seconds; attention evaluates waiting-input rules inside the terminal reader thread; cleanup removes expired transfer files and preview cache hourly.

Seven main data flows:

· Terminal flow: xterm.js keypress → SocketIO input → Paramiko channel send() → remote; remote recv() → reader thread → SocketIO output → xterm write.
· Status flow: monitor thread → TCP probe → in-memory state table → SocketIO host_status → frontend status light.
· File flow: source SFTP get → transfer storage → target SFTP put (or streaming direct transfer without disk write), progress pushed via SocketIO.
· Waiting-input flow: reader thread line buffer → rule engine → SocketIO session_attention / session_attention_cleared → frontend badge.
· Auth flow: startup generates password/SECRET_KEY/BOOT_ID → login page → session write → before_request double validation.
· Input flow: system keyboard / in-app virtual keyboard → unified key event → terminal or form; virtual keyboard switches with "Windows/Mac mode."
· Preview flow: remote file → SFTP read (or on-demand download to preview cache) → backend format conversion → frontend previewer render (native <img>/<video>/<audio>, PDF.js, Office conversion, Markdown render).

3. English UI Standard

3.1 Scope

All user-facing copy in English: login page, desktop icon names, window titles, menu items, buttons, form labels, validation messages, error messages, status text ("Online", "Offline", "Waiting for input", "Transferring", "Session remaining"), all settings options, virtual keyboard key labels and descriptions, file type names ("Image", "Video", "PDF Document", "Word Document", "Spreadsheet"), file browser toolbar, previewer prompts ("Preview not supported", "File too large, showing first 2 MB"), transfer states ("Queued", "Transferring 45%", "Completed", "Failed").

Kept as-is: internal code identifiers, SocketIO event names, logs, database field names, SSH commands themselves, file names (preserved as-is).

3.2 Implementation

No runtime i18n switching (single language, English). Use a centralized copy dictionary + Jinja global function:

· app/i18n.py maintains EN = {...} with semantic keys (login_title, btn_connect, status_online) and English values.
· Jinja global function t('login_title'); templates write {{ t('login_title') }}.
· Frontend JS reads the same dictionary via injected window.I18N = {...}, keeping frontend and backend copy consistent.
· Dynamic copy uses placeholders: t('attention_waiting', prompt='sudo password').

Benefit: copy changes in one place; adding another language later only requires extending the dictionary, no template changes.

3.3 English Typography Details

· Font stack: "Inter", "Segoe UI", "Helvetica Neue", Arial, sans-serif; terminal uses monospace ("JetBrains Mono", "Cascadia Code", monospace).
· Punctuation: UI copy uses standard English half-width punctuation (, . : ;); code/paths/commands also half-width.
· Line breaking: buttons and titles use word-break: normal; long file names use text-overflow: ellipsis.
· Time format: YYYY-MM-DD HH:mm:ss; relative time in English ("3 minutes ago", "2 hours ago").
· Capitalization: window titles and buttons use Title Case ("File Transfer", "Connect"); status text uses Sentence case ("Waiting for input", "Transferring"); all-caps only for short badge labels ("ONLINE", "OFFLINE").

3.4 Affected UI Inventory (confirmed English)

Login page: Web OS Login, Password, Sign in, Password is printed in the server console at startup. Restart the service if you forgot it.

Desktop: icon names are host names (user-defined); context menu Open Terminal, Browse Files, Edit Host, Delete Host; blank-area menu New Note, File Transfer, Quick Actions, Host Manager, System Settings.

Taskbar/Top bar: Start, Online 3 / 5, 2 waiting for input, Session expires in 6 days.

Terminal window: title is host name; status bar Connected, Connecting, Disconnected, Latency 23 ms; waiting badge Waiting for input: sudo password.

Notepad: Notepad, Send to Terminal, Copy, Clear.

File browser / Transfer: File Transfer, Storage, Remote Files, Upload, New Folder, Refresh, Name, Size, Modified, Preview, Download, Send to Server, Rename, Delete; transfer queue Queued, Transferring, Completed, Failed; confirmation Delete this file? This cannot be undone.

Previewer: Preview, Unsupported format, File too large, showing first 2 MB, Conversion failed, Close, Download.

Quick Actions: Quick Actions, categories User Management, Service Management, Disk, Network; Run, Output, Invalid parameter.

Host Manager: Host Manager, Add Host, Name, Host, Port, Username, Auth Method, Password, Key File, Save, Cancel.

System Settings: System Settings, Input & Keyboard, Keyboard Mode (Windows / Mac), Keyboard Strategy (Coexist / Replace), Custom Keys, Terminal Font Size, Touch Gestures, Waiting Input Rules, Import, Export, Test Sample, Enable, Confidence, Priority.

Virtual keyboard: key labels Esc Tab Ctrl Alt Shift Enter Backspace Fn; descriptions in English ("Sticky modifier: tap to lock").

4. Mobile Adaptation

4.1 Three Screen Forms and Layout Strategy

The frontend detects viewport width and orientation, classifying into three forms via CSS media queries + JS layout class switching:

Form Condition Layout strategy
Desktop Width ≥ 1024px Full desktop: icon grid + floating windows + bottom taskbar + top bar
Tablet 768px ≤ width < 1024px Simplified desktop: smaller icon grid, windows maximize by default, taskbar and top bar retained
Phone Width < 768px Single full-screen window mode: desktop becomes a "host list page," windows switch full-screen, bottom nav bar replaces taskbar

Core change in phone form: floating windows are unusable on phones (no drag space); instead use full-screen single window + app switcher. The desktop layer becomes a page of host cards; tapping a host enters full-screen terminal; a bottom nav bar fixes four entries: Hosts, Terminal (recent sessions), Files, Settings. Window switching happens via the nav bar or gestures (swipe left to go back), no more "stacked floating windows."

Orientation adaptation: listen to orientationchange and resize, recalculate layout class. Terminal in landscape fills the screen with the virtual keyboard occupying the lower half or floating; in portrait, terminal occupies the upper half and virtual keyboard fixes to the lower half (see 5.3). Tablet landscape approximates desktop, portrait approximates phone.

4.2 Touch Interaction Adaptation

· Touch targets: all tappable elements minimum 44×44px (Apple HIG and Material standard), icon spacing increased.
· Gestures: pinch-zoom font size in terminal, single-finger scrollback, long-press to select text (mobile xterm needs an alternative to rightClickSelectsWord, use long-press selection); swipe left to go back; swipe from screen edge to open app switcher.
· Drag degradation: desktop "drag notepad to terminal" is not feasible on phone; instead use a "Send to Terminal" button in the notepad window → select target session → confirm. Drag remains for tablet and desktop.
· Input focus: tapping the terminal area focuses a hidden textarea (xterm's standard approach), summoning the system keyboard or in-app virtual keyboard (see Section 5).

4.3 Terminal Layout on Phone

Phone terminal is full-screen; the core tension is "small screen + keyboard occupying space." Strategy:

· Terminal area auto-sizing: use the visualViewport API to monitor the visible area height after keyboard popup; terminal container height = visible height − top status bar. When keyboard pops, terminal shrinks and scrolls to bottom, not covering the current input line.
· Landscape: terminal occupies upper half, virtual keyboard floats in lower half or as a collapsible bottom panel; system keyboard popup shrinks terminal.
· Portrait: terminal occupies upper half, virtual keyboard fixes to lower half (collapsible to a toolbar), system keyboard and virtual keyboard are mutually exclusive (see 5.4).
· Status bar slimming: on phone, the terminal window top status bar keeps only host name + connection state + waiting-input badge; latency and other secondary info collapsed.

5. Virtual Keyboard Design

5.1 Why an In-App Virtual Keyboard

Phone system keyboards lack terminal-essential keys: Esc, Tab, Ctrl, Alt, arrow keys, |, ~, `, \, {}, [], etc. Even with the system keyboard's symbol page, input efficiency is extremely low. The app must provide a terminal-specific virtual keyboard covering missing keys, supporting coexistence with or replacement of the system keyboard.

5.2 Keyboard Layout: Windows Mode vs Mac Mode

Users can switch "keyboard mode" in settings, affecting modifier semantics and display:

Item Windows mode Mac mode
Modifier display Ctrl Alt Win Ctrl Option Cmd
Copy/Paste Ctrl+C / Ctrl+V Cmd+C / Cmd+V
Interrupt Ctrl+C sends \x03 Ctrl+C sends \x03 (terminal semantics identical, UI hint follows Mac conventions)
Line start/end Home / End Cmd+← / Cmd+→ hint
Delete to line start Ctrl+U Cmd+Delete hint

Note: the bytes actually sent to the remote in the terminal are determined by the shell; Mac mode mainly affects UI display and shortcut hints, plus copy/paste combo mapping. When a modifier is pressed, highlight "Ctrl is pressed" in the terminal area; tapping a letter then sends the corresponding control character.

5.3 Layered Virtual Keyboard Layout

The virtual keyboard is not a full 104-key drawing but layered + extensible:

Layer 1 (persistent toolbar): a horizontal toolbar at the bottom of the terminal window with the highest-frequency terminal-specific keys: Esc Tab Ctrl Alt ↑ ↓ ← → | ~ / - _ : " ' and an "Expand full keyboard" button. This layer is always visible in phone portrait; collapsible in landscape.

Layer 2 (full keyboard panel): pops up on "Expand," containing:

· Letter row, number row, symbol row (`!@#$%^&*()_+-=[]{}|;:'",.<>/?~``).
· Modifiers: Ctrl Alt Shift Esc Tab Enter Backspace.
· Function key row: F1–F12 (folded under "Fn").
· Arrow key area.
· Combo shortcut buttons: Ctrl+C, Ctrl+D, Ctrl+Z, Ctrl+L, Ctrl+A, Ctrl+E, Ctrl+U, Ctrl+K, Ctrl+R, plus Mac-mode Cmd+C/Cmd+V/Cmd+K etc.

Layer 3 (custom keys): settings page allows adding "My Common Keys"—a string or control sequence, appearing on the toolbar. E.g. sudo !!, \x1b[A (up arrow), tmux attach. The advantage of a personal tool is keys can be fully customized to your habits.

5.4 Relationship with System Keyboard

Two modes, user-selectable in settings:

Coexist mode (default): tapping the terminal summons the system keyboard for normal character input; the virtual keyboard toolbar persists below the terminal, providing only keys the system keyboard lacks (Esc, Tab, Ctrl, arrows, symbols). System keyboard and toolbar visible simultaneously; terminal height calculated dynamically via visualViewport.

Replace mode: does not summon the system keyboard; all input via the in-app virtual keyboard (including alphanumerics). Suitable for cases where the system keyboard severely obscures or the user wants unified feel. In replace mode, virtual keyboard occupies lower half, terminal upper half.

Both modes switchable anytime via a "keyboard" icon button on the toolbar.

5.5 Modifier State Machine

Ctrl, Alt, Shift are "sticky keys": tap to enter pressed state (highlighted), tap again to cancel; pressing a letter while in pressed state sends the corresponding control character and auto-releases the modifier (unless double-tapped to lock). This state machine must be maintained uniformly in the frontend; terminal area and virtual keyboard share the same state, preventing modifier state loss during system keyboard input.

5.6 Hardware Keyboard Access

When a phone/tablet connects an external Bluetooth keyboard, keydown events fire normally; the virtual keyboard can auto-collapse (hide the toolbar upon detecting physical keyboard input; user can manually recall). Windows/Mac mode switching also applies to external keyboard shortcut hints.

6. Module Design Details

6.1 Startup Entry (main.py)

argparse parses --host, --port, --debug. On startup: generate random password/SECRET_KEY/BOOT_ID (memory only); create storage/ (chmod 700) and cache/preview/, cache/remote/; initialize SQLite tables (hosts, actions, staged_files, attention_rules); start monitor and cleanup threads. Print English startup info and the current password, start SocketIO.

Production uses gunicorn + eventlet single worker; development uses python main.py.

6.2 Authentication and Session (auth.py)

Password via secrets.token_urlsafe(12), printed to console, stored in memory. On successful login write boot_id, login_at, session.permanent = True, 7-day validity. before_request double validation (boot_id mismatch / over 7 days → clear session, redirect to login). SECRET_KEY random each startup, old cookie signatures invalid. Login page English prompt: "Password is printed in the server console at startup. Restart the service if you forgot it."

6.3 SSH Terminal (ssh_manager.py + sockets.py)

Each terminal gets a Paramiko invoke_shell() channel, stored in {sid: channel} with locking. Frontend keypress → input event → channel.send(); reader thread recv(4096) → output event → xterm. Disconnect retains 10 minutes for reconnect. Status bar in English: "Connected / Connecting / Disconnected", latency.

6.4 Notepad and Cross-Terminal Paste

Notepad content written to frontend global store. Desktop/tablet: drag to terminal auto-pastes; copy-paste fallback reads store then system clipboard. Phone: notepad window "Send to Terminal" button → select target session. Reverse: xterm selection writes store + attempts system clipboard.

6.5 Online Monitoring (monitor.py)

Every 30 seconds socket.create_connection((host, 22), timeout=3), result in memory, push host_status on change. Frontend status light green/red/grey, offline icons greyed out, top bar English "Online 3 / 5".

6.6 Parameterized Quick Actions (actions.py)

actions table stores name, category, command_template, arguments_json. Frontend dynamically renders form; backend validates parameters with the same rules (regex whitelist / option list), renders template, then exec_command executes. Built-in "Add User", "Check Disk", "Restart Service", "Check Memory", extensible via configuration. Window left category list (User Management, Service Management, Disk, Network), right form + Run button + English output area.

6.7 File Transfer and Relay (files.py)

storage/ staging area, staged_files table records metadata. Three modes: A disk-relay (default), B streaming direct (no disk write), C source-side scp (requires key trust). Async task queue + progress push. 24h cleanup. Window left-right split (mobile top-bottom stack): left staging list (previewable), right target server + path + mode selection, bottom transfer queue with English states ("Queued / Transferring 45% / Completed / Failed").

6.8 File Browser and Preview (files_browser.py + preview.py)

files_browser.py: provides directory listing endpoints for two data sources—/api/files/local?path= (transfer directory) and /api/files/remote?sid=&path= (SFTP session). Returns [{name, type, size, mtime, ext}]. Supports new directory, rename, delete (double confirm), upload (local→transfer/remote), download (remote/transfer→local browser).

preview.py: preview endpoint /api/preview?source=local|remote&path=&sid=. Dispatches by extension: images/audio/video streamed (video supports Range); PDF returns file stream; text returns truncated text; Markdown returns source; Office goes through LibreOffice to PDF with caching; archives return entry name list; unknown types return 415 "Preview not supported". All endpoints pass session validation + path normalization validation.

Frontend previewer component: preview.js renders by type—<img>, <video controls>, <audio controls>, PDF.js canvas, marked + DOMPurify Markdown view, <pre> text view, Office PDF view, archive entry table, unsupported prompt. Mobile full-screen preview + back button.

Supported preview formats:

Type Extensions Method Dependency
Image jpg/jpeg/png/gif/webp/svg/bmp Backend streaming, <img> render None
Video mp4/webm/ogg/mov (mov browser-dependent) Backend HTTP Range streaming, <video> None
Audio mp3/wav/ogg/flac Backend streaming, <audio> None
PDF pdf Backend file stream, PDF.js render PDF.js (frontend lib)
Markdown md/markdown Backend source, marked.js + DOMPurify marked + DOMPurify
Text/code txt/log/conf/py/js/json/yaml/sh etc. Backend text (size-limited), <pre> + optional highlight Optional highlight.js
Word doc/docx LibreOffice --headless --convert-to pdf, PDF.js render LibreOffice
Excel xls/xlsx LibreOffice to PDF or HTML table LibreOffice
PowerPoint ppt/pptx LibreOffice to PDF, PDF.js render LibreOffice
Archive zip/tar/gz No content preview, list entry names stdlib
Other unknown Show "Preview not supported, download to transfer storage" None

Office preview engineering trade-off: Office documents cannot render natively in the browser. Two paths: Path 1: LibreOffice server-side conversion (recommended)—install LibreOffice on the server; on preview request, write file to temp dir, run soffice --headless --convert-to pdf --outdir <tmp> <file>, return PDF to frontend for PDF.js render. Cache by file hash (cache/preview/<sha256>.pdf); same file not re-converted. High fidelity, simple frontend; cost is LibreOffice install (~hundreds of MB), first conversion 1–3s latency. Path 2: pure frontend libraries (docx-preview, SheetJS)—no server dependency but limited fidelity (complex layout, charts, formulas distorted), large files consume frontend memory. Recommendation: default Path 1; settings offer "Office preview method" toggle (LibreOffice conversion / simple frontend render / disable preview, download only). Personal server installing LibreOffice is acceptable; fidelity matters most.

Preview loading and caching: On-demand loading—single-click file opens previewer on right/popup, no preloading. Size limits—text preview limited to 2MB (truncate with "File too large, showing first 2 MB"); images/video/PDF unlimited but streamed (HTTP Range for video seeking). Preview cache—Office-to-PDF results and text highlight results stored in cache/preview/, named by file SHA-256; cleanup thread removes cache older than 24h hourly. Remote files—previewing remote files: backend reads via SFTP to temp file then processes; small files (<10MB) cached in cache/remote/, large files streamed without disk write (video uses SFTP segmented read + Range response).

Security boundaries: preview endpoints must validate login session (same before_request). Path parameters normalized, .. traversal forbidden, only transfer directory or current SFTP session authorized remote paths allowed. LibreOffice conversion runs in isolated temp dir with 30s timeout; failure returns "Conversion failed". SVG preview via <img> not inline render (XSS prevention); Markdown rendered then sanitized via DOMPurify. Archives only read entry names, never extracted/executed.

6.9 Host Management (hosts.py)

hosts table: id, name, host, port, username, auth_method, key_path, password_encrypted, group. Password Fernet-encrypted (key derived from memory, re-entry needed after restart). Table + CRUD form, English labels.

6.10 Waiting-Input Detection (attention.py + attention_rules.py)

Goal: detect whether the remote is waiting for input (sudo password, y/n confirmation, passwd, Python REPL, re-prompt after interrupt), show orange badge in window title bar and taskbar with English prompt "Waiting for input: sudo password". No auto-reply.

Principle: SSH transports a byte stream with no "state" field. Detection relies on heuristic inference from "prompt pattern + output silence + cursor position + command context + interrupt marker." Executed inside the backend reader thread; frontend timing is unreliable.

SessionState: last_output_at, line_buffer(last 2KB), last_line, pending_prompt, confidence, last_command, last_interrupt_at.

Evaluation: on data received update state, detect \x03; filter candidate rules (enabled + context features within window_ms); evaluate by match_mode; on hit start silence_ms timer, on expiry with no new data and closing conditions met → push session_attention; new data not satisfying → push cleared.

Frontend: high confidence solid badge + text, medium semi-transparent, low log only; taskbar tab same-color dot; top bar summary "2 sessions waiting for input" clickable to jump.

7. Waiting-Input Rule Data Standard

7.1 Table Structure

```sql
CREATE TABLE attention_rules (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL UNIQUE,
    enabled         INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
    builtin         INTEGER NOT NULL DEFAULT 0 CHECK (builtin IN (0,1)),
    match_mode      TEXT    NOT NULL DEFAULT 'any'
                    CHECK (match_mode IN ('any','all','sequence')),
    patterns        TEXT    NOT NULL,
    silence_ms      INTEGER NOT NULL DEFAULT 400
                    CHECK (silence_ms BETWEEN 0 AND 5000),
    confidence      TEXT    NOT NULL DEFAULT 'high'
                    CHECK (confidence IN ('high','medium','low')),
    priority        INTEGER NOT NULL DEFAULT 100
                    CHECK (priority BETWEEN 0 AND 9999),
    window_ms       INTEGER NOT NULL DEFAULT 30000
                    CHECK (window_ms BETWEEN 0 AND 600000),
    sample          TEXT    DEFAULT '',
    description     TEXT    DEFAULT '',
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_attention_enabled_priority ON attention_rules(enabled, priority DESC);
```

Constraints: name unique 1–64 chars; silence_ms 0–5000; priority larger is higher; window_ms default 30s max 10min; builtin=1 cannot be deleted, only disabled or copied; updated_at written by application layer.

7.2 Enums

· match_mode: any (any hit) / all (all hit) / sequence (ordered appearance).
· confidence: high (solid badge + text) / medium (semi-transparent) / low (log only).
· pattern.type:

type Required Semantics Constraints
regex value Regex match line buffer tail Valid Python regex, compile cache, input truncated to last 2048 chars
contains_any values Contains any keyword (case-insensitive) Array 1–20 items, each 1–64 chars
line_end_no_newline none Last line does not end with \n Boolean
cursor_not_at_col0 none Cursor not at column 0 Boolean
command_context value Recent command matches regex Valid regex, valid within window_ms
after_interrupt none Recent Ctrl+C Boolean, valid within window_ms

7.3 patterns JSON Schema

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "AttentionRulePatterns",
  "type": "array", "minItems": 1, "maxItems": 10,
  "items": { "oneOf": [
    { "type": "object", "required": ["type","value"],
      "properties": { "type": {"const":"regex"}, "value": {"type":"string","minLength":1,"maxLength":512},
                      "flags": {"type":"string","enum":["","i","m","im"]} },
      "additionalProperties": false },
    { "type": "object", "required": ["type","values"],
      "properties": { "type": {"const":"contains_any"},
                      "values": {"type":"array","minItems":1,"maxItems":20,
                                 "items":{"type":"string","minLength":1,"maxLength":64}} },
      "additionalProperties": false },
    { "type": "object", "required": ["type"],
      "properties": { "type": {"enum":["line_end_no_newline","cursor_not_at_col0","after_interrupt"]} },
      "additionalProperties": false },
    { "type": "object", "required": ["type","value"],
      "properties": { "type": {"const":"command_context"}, "value": {"type":"string","minLength":1,"maxLength":512},
                      "flags": {"type":"string","enum":["","i"]} },
      "additionalProperties": false }
  ]}
}
```

Cross-field constraints (application layer): sequence excludes context features and requires at least one each of regex/contains_any; all max 6 items; rules with context features require window_ms ≥ 1000.

7.4 Application-Layer Validation

Name 1–64 unique → JSON parse array 1–10 items → per-item Schema validation (regex trial compile, contains_any dedupe/remove empty) → three cross-field rules → numeric ranges → total rules ≤200, regex cache LRU 256 → if sample non-empty, run match as non-blocking hint.

7.5 Built-in Rules

sudo password, y/n confirmation, passwd interaction, Python REPL prompt, re-prompt after Ctrl+C (builtin=1, JSON same as previous version).

7.6 Import/Export

{"format":"webos-attention-rules","version":1,"exported_at":"...","rules":[...]}. Import validates format/version, per-item 7.4 validation, name conflict skip, builtin forced to 0.

7.7 Settings Page Interaction

Rule list (name, enabled, confidence, priority, hit count, CRUD, import/export); edit form (name, enabled, match_mode, silence_ms, confidence, priority, window_ms, feature list, regex live validation); sample test area (paste reference output such as Python sample or Which to like prompt, click Test to return hit/miss/hit feature/confidence); live preview (active session read-only badge preview). All English labels.

8. UI Design Principles

Desktop/tablet form four layers: desktop layer (host icon grid + status light + context menu; blank right-click opens Notepad/File Transfer/Quick Actions/Host Manager/System Settings) → window layer (unified components, six window types: Terminal, Notepad, File Browser/Transfer, Quick Actions, Host Manager, System Settings; position/size remembered in localStorage) → taskbar (Start button, window tabs, status area: online count / transfer count / waiting count / session remaining) → top bar status area (identity, time, global status).

Phone form: host list page / full-screen window → bottom nav bar (Hosts, Terminal, Files, Settings) → top slim status bar. Floating windows and drag disabled; full-screen switching + app switcher, swipe left to go back.

File browser UI: top path breadcrumb + toolbar (Upload, New Folder, Refresh, View switch); middle file list (icons colored by type, name, size, modified time; single-click select, double-click preview, right-click menu Preview/Download/Send to Server/Rename/Delete); right/popup previewer. Mobile list full-width, preview full-screen.

Visual: dark background #1e1e2e, window #2a2a3c, accent cyan-blue #4fc3f7, waiting-input orange #ff9800, status light green/red/grey, radius 8px, transitions 150–200ms. English sans-serif font stack, monospace terminal. Touch targets ≥44px, mobile font ≥14px.

9. Directory Structure

```
webos/
├── main.py
├── config.py
├── app/
│   ├── __init__.py
│   ├── i18n.py              # English copy dictionary + t() function
│   ├── auth.py
│   ├── ssh_manager.py
│   ├── hosts.py
│   ├── actions.py
│   ├── files.py             # transfer and relay
│   ├── files_browser.py     # local/remote directory listing and file ops
│   ├── preview.py           # preview endpoint and LibreOffice conversion
│   ├── monitor.py
│   ├── cleanup.py           # expired transfer files and preview cache cleanup
│   ├── attention.py
│   ├── attention_rules.py
│   ├── sockets.py
│   └── routes.py
├── templates/
│   ├── base.html
│   ├── login.html
│   ├── desktop.html
│   └── windows/             # terminal / notepad / files / actions / hosts / settings / preview
├── static/
│   ├── js/                  # desktop.js, layout.js, keyboard.js, terminal.js, notepad.js,
│   │                        # files.js, preview.js, transfer.js, actions.js, settings.js
│   └── css/
├── storage/                 # transfer space (chmod 700)
├── cache/
│   ├── preview/             # Office-to-PDF, text highlight cache
│   └── remote/              # remote small file preview cache
└── webos.db
```

10. Implementation Roadmap

Phase 1: Skeleton and Auth. main.py + create_app + i18n dictionary + random password + English login page (mobile-adapted) + double validation + English console startup info. Verify: login on desktop and phone, restart invalidates, UI all English.

Phase 2: Responsive Desktop Shell and Host Management. desktop.html + layout.js three forms + window manager (desktop/tablet) + phone host list and bottom nav + host CRUD. Verify: three-form layout correct, rotation reflows, add/delete hosts.

Phase 3: Terminal Connectivity. ssh_manager + sockets + xterm + visualViewport. Verify: terminal interactive on desktop and phone, keyboard doesn't cover input line.

Phase 4: Virtual Keyboard. keyboard.js three layers + sticky keys + Windows/Mac + coexist/replace + custom keys + external keyboard detection. Verify: phone inputs Esc/Tab/Ctrl+C/arrows/|/~, Mac mode hints change, custom keys work.

Phase 5: Notepad and Cross-Terminal Paste. Frontend store + desktop drag + phone button send + copy-paste fallback. Verify: drag and button both work, selection enters notepad.

Phase 6: Online Monitoring and Status Light. monitor + push + status light + top bar online count. Verify: shutdown changes to red within 30s, phone cards sync.

Phase 7: Parameterized Quick Actions. Hardcode 2 to prove form render/execution → SQLite config + parameter validation + mobile touch adaptation. Verify: Add User succeeds, invalid parameters rejected.

Phase 8: File Transfer and Relay. Mode A loop → Mode B direct + queue progress + 24h cleanup + mobile top-bottom stack. Verify: A machine pull to transfer then push to B, phone can complete.

Phase 9: File Browser and Preview. files_browser local/remote listing + preview dispatch (image/video/audio/PDF/text/Markdown/archive) + LibreOffice Office conversion and cache + frontend preview.js type rendering + mobile full-screen preview + security validation. Verify: remote image direct view, video seeks, PDF renders, Word/Excel/PPT convert to PDF preview, Markdown sanitized render, text truncation prompt, archive lists entries, unknown type prompts download; mobile full-screen preview back works.

Phase 10: Waiting-Input Detection. attention rule engine + built-in rules + attention_rules CRUD validation + settings page rule management (English) + frontend badge and taskbar highlight + phone top waiting count. Verify: sudo/y/n high confidence, Python medium confidence, re-prompt after Ctrl+C, custom Which to like rule sample test, import/export, phone badge click jumps.

Wrap-up: styling polish, window layout memory, mobile gestures and performance, English copy full audit, security hardening (HTTPS, parameter validation, preview path validation), deployment docs.

11. Security Boundaries

Transfer directory chmod 700, transfer logs retained, 24h cleanup. Quick action parameters forced whitelist, command templates only predefined placeholders. Waiting-input rule regex compile cache + input truncation + rule cap 200 + sample test in isolated thread with timeout. Virtual keyboard custom keys sent only as input strings, not rendered through command templates. Preview endpoints all pass session validation + path normalization (.. traversal forbidden) + only transfer directory or current SFTP session authorized paths + SVG via <img> for XSS prevention + Markdown via DOMPurify + LibreOffice conversion isolated temp dir with 30s timeout + archives only read entry names without extraction. Production must use HTTPS. Single user no password recovery, forget = restart. SECRET_KEY random each startup, old cookies invalid, BOOT_ID and 7-day double insurance.

12. Design Trade-off Summary

Window manager self-written minimal version. Desktop/tablet floating windows, phone full-screen + switcher, shared window components.

Desktop only hosts, features in Start menu/bottom nav. Focus is machines.

Notepad: desktop drag, phone button. Same frontend store, two entry points.

Transfer space is a dispatch station. Default disk write, direct transfer when interoperable, 24h cleanup.

Auth without persistence is a feature. Random password + memory state + restart invalidation.

Waiting-input detection is heuristic hinting. Multi-signal inference, rules configurable and sample-testable, hint only no auto-reply.

Virtual keyboard is essential on mobile. Completes modifiers and control keys; Windows/Mac only affects display and combo hints.

English UI via centralized dictionary. Single language no runtime switch, t() shared frontend/backend, copy changes in one place.

Office preview defaults to server-side LibreOffice to PDF. Fidelity first, installing hundreds of MB for format accuracy; offers frontend simple render and disable preview as fallbacks.

One-sentence summary: Backend single Flask process handles connections, monitoring, detection, configuration, transfer, and preview; frontend responsive English desktop shell handles windows, navigation, gestures, virtual keyboard, and file preview; desktop, tablet, phone landscape/portrait are four forms of the same system; UI all English, files viewable in-system, identity without persistence, waiting input hinted by configurable rule engine. This is the complete design principle and implementation path for Web OS.
