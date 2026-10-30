/* terminal.js — xterm windows over SocketIO, reattach replay, status bar,
   latency, selection → clipboard + notepad store, copy/paste keys,
   wheel-scroll fix + Shift+Scroll scrollback override, host-bound Quick
   Actions / Files shortcuts, visualViewport keyboard avoidance
   (IDEA.md §6.3, §4.3). */
(function () {
  'use strict';
  var OS = window.OS;
  var $ = OS.$;

  var term = OS.term = {
    sessions: new Map(),   // sid -> session
    initialized: false,

    init: function () {
      if (term.initialized || !OS.socket) return;
      term.initialized = true;
      OS.socket.on('terminal_output', function (data) {
        var s = term.sessions.get(data.sid);
        if (s) { s.term.write(data.data); trackModes(s, data.data); }
      });
      OS.socket.on('terminal_replay', function (data) {
        var s = term.sessions.get(data.sid);
        if (s && !s.replayed) {
          s.term.write(data.data);
          trackModes(s, data.data);
          s.replayed = true;
        }
      });
      OS.socket.on('terminal_meta', function (meta) {
        var s = term.sessions.get(meta.sid);
        if (s) applyState(s, meta.state);
        if (meta.attention && !s) {
          // session signalled in another tab; nothing to badge here
        }
      });
      OS.socket.on('terminal_closed', function (data) {
        var s = term.sessions.get(data.sid);
        if (s) applyState(s, 'closed');
      });

      // refit on form/orientation changes and keyboard popups
      window.addEventListener('resize', debounce(fitActive, 150));
      if (window.visualViewport) {
        visualViewport.addEventListener('resize', debounce(fitActive, 100));
      }
      OS.layout.onChange(function () { setTimeout(fitActive, 60); });
    },

    open: function (hostId, opts) {
      var host = OS.hostById(hostId);
      var name = host ? host.name : 'terminal';
      // every open launches a fresh terminal (multiple per host); callers
      // that re-enter an existing session pass {reuse: true}
      if (opts && opts.reuse) {
        var existing = null;
        term.sessions.forEach(function (s) {
          if (s.hostId === hostId && s.state !== 'closed') existing = s;
        });
        if (existing) {
          existing.win.el.classList.remove('hidden-win');
          OS.wm.focus(existing.win);
          return;
        }
      }
      OS.socket.emit('terminal_create', {host_id: hostId, cols: 80, rows: 24},
        function (ack) {
          if (!ack || !ack.ok) {
            OS.toast((ack && ack.message) || 'connect failed', true);
            if (ack && ack.error === 'needs_password') OS.openHostManager(hostId);
            return;
          }
          term.build(ack.sid, hostId, ack.name || name);
        });
    },

    build: function (sid, hostId, name) {
      var s = {
        sid: sid, hostId: hostId, hostName: name, win: null,
        state: 'connecting', replayed: false, term: null, fit: null
      };
      term.sessions.set(sid, s);
      // wm.create triggers OS.winInit.terminal(win) synchronously; it uses
      // the `win` argument, and s.win is wired right after.
      var win = OS.wm.create('terminal', {
        sid: sid, hostId: hostId, title: name
      });
      s.win = win;
      applyState(s, s.state);
      OS.socket.emit('terminal_attach', {sid: sid});
      return s;
    },

    attachExisting: function (meta) {
      if (term.sessions.has(meta.sid)) return;
      var s = {
        sid: meta.sid, hostId: meta.host_id, hostName: meta.name, win: null,
        state: meta.state, replayed: false, term: null, fit: null
      };
      term.sessions.set(meta.sid, s);
      var win = OS.wm.create('terminal', {
        sid: meta.sid, hostId: meta.host_id, title: meta.name
      });
      s.win = win;
      win.el.classList.add('hidden-win');
      OS.wm.renderTabs();
      applyState(s, meta.state);
      OS.socket.emit('terminal_attach', {sid: meta.sid});
    },

    sendInput: function (sid, text) {
      if (!text) return;
      OS.socket.emit('terminal_input', {sid: sid, data: text});
    },

    closeSession: function (sid) {
      var s = term.sessions.get(sid);
      if (s && s.onClose) s.onClose();
      OS.socket.emit('terminal_close', {sid: sid});
      if (s) {
        term.sessions.delete(sid);
        OS.wm.close(s.win);
      }
      delete OS.state.attention[sid];
      OS.updateTopbar();
    }
  };

  // live theme switching: restyle every open terminal in place
  OS.bus.on('theme', function () {
    term.sessions.forEach(function (s) {
      if (s.term) {
        try { s.term.options.theme = OS.theme.xtermTheme(); }
        catch (e) { /* older xterm builds — new windows pick it up anyway */ }
      }
    });
  });

  OS.winInit.terminal = function (win) {
    var sid = win.opts.sid;
    var s = term.sessions.get(sid);
    if (!s || s.term) return;
    var screen = $('.term-screen', win.body);
    var fontSize = parseInt((OS.store.get('terminal-font-size', 14)), 10) || 14;

    var xterm = new Terminal({
      fontSize: fontSize,
      fontFamily: '"JetBrains Mono", "Cascadia Code", "SF Mono", Menlo, Consolas, monospace',
      cursorBlink: true,
      theme: OS.theme.xtermTheme(),
      scrollback: 5000,
      allowProposedApi: true
    });
    var fit = new FitAddon.FitAddon();
    xterm.loadAddon(fit);
    xterm.open(screen);
    s.term = xterm;
    s.fit = fit;

    xterm.onData(function (data) {
      term.sendInput(sid, data);
    });

    // selection → notepad store + system clipboard (best effort: selecting
    // text is copying it, like a native terminal)
    xterm.onSelectionChange(function () {
      var sel = xterm.getSelection();
      if (sel) {
        OS.bus.emit('selection', {sid: sid, text: sel});
        copyText(sel);
      }
    });

    // clipboard (IDEA.md §6.4): paste via Ctrl+V / Ctrl+Shift+V / Cmd+V /
    // right-click; copy via Ctrl+C / Ctrl+Shift+C / Cmd+C while text is
    // selected — without a selection Ctrl+C still sends the interrupt.
    xterm.attachCustomKeyEventHandler(function (ev) {
      if (ev.type !== 'keydown') return true;
      if (ev.key === 'v' && !ev.altKey && (ev.ctrlKey || ev.metaKey)) {
        doPaste();
        return false;
      }
      if (ev.key === 'c' && !ev.altKey && (ev.ctrlKey || ev.metaKey) &&
          xterm.hasSelection()) {
        doCopy();
        return false;
      }
      return true;
    });
    screen.addEventListener('contextmenu', function (e) {
      e.preventDefault();
      doPaste();
    });
    function doPaste() {
      xterm.focus();
      pasteInto(sid);
    }
    function doCopy() {
      copyText(xterm.getSelection());
    }

    // xterm.js maps the wheel to arrow keys whenever the active buffer
    // reports no scrollback — which is also true of a normal buffer that
    // has not yet filled one screen, making the wheel walk shell history
    // instead of scrolling. Swallow the wheel when there is nothing to
    // scroll; real scrollback and alternate-screen apps (vim/less) keep
    // xterm's own handling.
    screen.addEventListener('wheel', function (e) {
      var buf = xterm.buffer.active;
      if (buf.type === 'alternate' || buf.length > xterm.rows) return;
      e.preventDefault();
      e.stopPropagation();
    }, {capture: true, passive: false});

    // Shift + Scroll reaches what the plain wheel cannot. Apps that take
    // over the screen or enable mouse tracking (Claude Code fullscreen,
    // tmux with mouse on) turn the wheel into app input, stranding the
    // terminal's own scrollback; xterm.js ignores Shift+wheel entirely
    // (getLinesScrolled returns 0 for it), so it is free to claim here
    // for both of xterm's wheel paths (mouse reporting and alternate
    // screen). Normal buffer → scroll the viewport; alternate screen has
    // no scrollback, so page the application instead (PgUp/PgDn scroll
    // Claude Code's transcript, vim, less…).
    if (xterm.attachCustomWheelEventHandler) {
      xterm.attachCustomWheelEventHandler(function (ev) {
        if (!ev.shiftKey) return true;
        var d = ev.deltaY || ev.deltaX;   // Chrome folds Shift+wheel into deltaX
        if (!d) return true;
        ev.preventDefault();
        if (xterm.buffer.active.type !== 'alternate') {
          var lines = wheelLines(ev, Math.abs(d), xterm);
          xterm.scrollLines(d < 0 ? -lines : lines);
          return false;
        }
        // accumulate trackpad drizzle, send one key per ~100px of travel
        s.wheelAcc = (s.wheelAcc && Math.sign(s.wheelAcc) === Math.sign(d))
          ? s.wheelAcc + Math.abs(d) : Math.abs(d);
        while (s.wheelAcc >= 100) {
          s.wheelAcc -= 100;
          term.sendInput(sid, d < 0 ? '\x1b[5~' : '\x1b[6~');
        }
        return false;
      });
    }

    // status-bar hint while the app owns the wheel; click jumps to the top
    var hintEl = $('.term-scroll-hint', win.body);
    hintEl.textContent = OS.t('term_scroll_hint');
    hintEl.title = OS.t('term_scroll_top');
    hintEl.addEventListener('click', function () {
      xterm.focus();
      if (xterm.buffer.active.type === 'alternate') {
        term.sendInput(sid, '\x1b[1;5H');   // Ctrl+Home for TUI transcripts
      } else {
        xterm.scrollToTop();
      }
    });

    // drag & drop paste from notepad (desktop/tablet, IDEA.md §6.4)
    screen.addEventListener('dragover', function (e) { e.preventDefault(); });
    screen.addEventListener('drop', function (e) {
      e.preventDefault();
      var text = e.dataTransfer.getData('text/plain');
      if (text) term.sendInput(sid, text);
    });

    // status bar bits
    var stateEl = $('.term-state', win.body);
    var latEl = $('.term-latency', win.body);
    var attEl = $('.term-attention', win.body);
    applyState(s, s.state);

    // host-bound shortcuts: Quick Actions and the file browser open with
    // this terminal's host preselected, so no host picking is needed
    var actionsBtn = $('.term-actions', win.body);
    var filesBtn = $('.term-files', win.body);
    actionsBtn.title = OS.t('ctx_quick_actions');
    actionsBtn.setAttribute('aria-label', actionsBtn.title);
    actionsBtn.appendChild(OS.icon('bolt'));
    actionsBtn.addEventListener('click', function () {
      if (OS.actions) OS.actions.open(win.opts.hostId);
    });
    filesBtn.title = OS.t('ctx_browse_files');
    filesBtn.setAttribute('aria-label', filesBtn.title);
    filesBtn.appendChild(OS.icon('folder'));
    filesBtn.addEventListener('click', function () {
      OS.files.open({hostId: win.opts.hostId});
    });

    // latency probe
    var latTimer = setInterval(function () {
      if (!win.el.isConnected || s.state === 'closed') return;
      var t0 = Date.now();
      OS.socket.emit('latency_probe', {}, function () {
        latEl.textContent = OS.t('latency_ms', {ms: Date.now() - t0});
      });
    }, 8000);

    // attention badge inside status bar
    var attUnsub = OS.bus.on('attention', function (data) {
      if (data.sid !== sid) return;
      attEl.hidden = false;
      attEl.textContent = OS.t('waiting_input', {prompt: data.hint || data.name});
    });
    var clrUnsub = OS.bus.on('attention-cleared', function (data) {
      if (data.sid !== sid) return;
      attEl.hidden = true;
    });

    s.fitNow = function () {
      if (!win.el.isConnected || s.state === 'closed') return;
      try {
        fit.fit();
        var dims = fit.proposeDimensions();
        if (dims && dims.cols > 2 && dims.rows > 2) {
          OS.socket.emit('terminal_resize',
            {sid: sid, cols: dims.cols, rows: dims.rows});
        }
      } catch (e) { /* not measurable yet */ }
    };
    setTimeout(s.fitNow, 60);
    var resUnsub = OS.bus.on('win-resized', function (w) {
      if (w === win) setTimeout(s.fitNow, 30);
    });

    // virtual keyboard toolbar (keyboard.js)
    if (OS.kb) OS.kb.attach(win, s);

    // per-window teardown — the shared winClose hook dispatches on w.opts.sid
    // because several terminal windows can be open at once
    s.onClose = function () {
      clearInterval(latTimer);
      clearTimeout(s.hintTimer);
      attUnsub();
      clrUnsub();
      resUnsub();
    };
    OS.winClose.terminal = function (w) {
      var closing = w.opts.sid;
      var sess = term.sessions.get(closing);
      if (sess) {
        if (sess.onClose) sess.onClose();
        term.sessions.delete(closing);
        // window close terminates the session (single-user semantics)
        OS.socket.emit('terminal_close', {sid: closing});
      }
    };
  };

  function applyState(s, state) {
    s.state = state;
    var win = s.win;
    if (!win || !win.el || !win.el.isConnected) return;
    var stateEl = $('.term-state', win.body);
    if (!stateEl) return;
    var map = {
      connected: [OS.t('st_connected'), 'on'],
      connecting: [OS.t('st_connecting'), ''],
      closed: [OS.t('st_disconnected'), 'off']
    };
    var m = map[state] || map.connecting;
    stateEl.textContent = m[0];
    stateEl.className = 'term-state ' + m[1];
  }

  // Sniff mouse-tracking DECSETs (1000/1002/1003) in the output stream so
  // the status bar can announce the Shift+Scroll escape hatch while an app
  // owns the wheel. The 24-char tail lets a sequence split across chunks
  // still match; replays re-apply the same final state, so it is idempotent.
  function trackModes(s, chunk) {
    if (!chunk || chunk.indexOf('\x1b[?') === -1) return;
    s.modeTail = (s.modeTail + chunk).slice(-24);
    var re = /\x1b\[\?(1000|1002|1003)(h|l)/g, m;
    while ((m = re.exec(s.modeTail))) s.mouseCapture = m[2] === 'h';
    scheduleHint(s);
  }

  // deferred one tick past xterm's async write parser so buffer.type is
  // already flipped when the hint decides
  function scheduleHint(s) {
    clearTimeout(s.hintTimer);
    s.hintTimer = setTimeout(function () { updateHint(s); }, 80);
  }

  function updateHint(s) {
    if (!s.win || !s.win.el || !s.win.el.isConnected) return;
    var el = $('.term-scroll-hint', s.win.body);
    if (!el) return;
    var alt = !!(s.term && s.term.buffer.active.type === 'alternate');
    el.hidden = !(alt || s.mouseCapture);
  }

  function wheelLines(ev, unit, xterm) {
    if (ev.deltaMode === 1) return Math.max(1, unit);                    // lines
    if (ev.deltaMode === 2) return Math.max(1, (xterm.rows || 24) - 1);  // pages
    return Math.min(30, Math.max(1, Math.round(unit / 40)));             // pixels
  }

  function copyText(text) {
    if (!text || !navigator.clipboard || !navigator.clipboard.writeText) return;
    try {
      navigator.clipboard.writeText(text).catch(function () {});
    } catch (e) { /* no clipboard access — selection stays in the store */ }
  }

  function pasteInto(sid) {
    function send(text) { if (text) term.sendInput(sid, text); }
    if (navigator.clipboard && navigator.clipboard.readText) {
      navigator.clipboard.readText().then(send).catch(function () {
        OS.toast(OS.t('term_clipboard_fail'), true);
      });
    } else {
      OS.toast(OS.t('term_clipboard_fail'), true);
    }
  }

  function fitActive() {
    var win = OS.wm && OS.wm._active;
    if (!win || win.type !== 'terminal') return;
    var sid = win.opts.sid;
    var s = term.sessions.get(sid);
    if (s && s.fitNow) s.fitNow();
  }

  function debounce(fn, ms) {
    var t = null;
    return function () {
      clearTimeout(t);
      t = setTimeout(fn, ms);
    };
  }
})();
