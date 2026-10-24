/* terminal.js — xterm windows over SocketIO, reattach replay, status bar,
   latency, selection → notepad store, paste, visualViewport keyboard
   avoidance (IDEA.md §6.3, §4.3). */
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
        if (s) s.term.write(data.data);
      });
      OS.socket.on('terminal_replay', function (data) {
        var s = term.sessions.get(data.sid);
        if (s && !s.replayed) {
          s.term.write(data.data);
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

    open: function (hostId) {
      var host = OS.hostById(hostId);
      var name = host ? host.name : 'terminal';
      // reuse a window if one is already open for this host
      var existing = null;
      term.sessions.forEach(function (s) {
        if (s.hostId === hostId && s.state !== 'closed') existing = s;
      });
      if (existing) {
        existing.win.el.classList.remove('hidden-win');
        OS.wm.focus(existing.win);
        return;
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

    // selection → notepad store + clipboard attempt
    xterm.onSelectionChange(function () {
      var sel = xterm.getSelection();
      if (sel) OS.bus.emit('selection', {sid: sid, text: sel});
    });

    // paste: Ctrl+Shift+V / Cmd+V / right-click (IDEA.md §6.4)
    xterm.attachCustomKeyEventHandler(function (ev) {
      if (ev.type !== 'keydown') return true;
      if (ev.key === 'v' && ev.ctrlKey && ev.shiftKey) { doPaste(); return false; }
      if (ev.key === 'v' && ev.metaKey) { doPaste(); return false; }
      return true;
    });
    screen.addEventListener('contextmenu', function (e) {
      e.preventDefault();
      doPaste();
    });
    function doPaste() {
      pasteInto(sid);
    }

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
    OS.bus.on('win-resized', function (w) {
      if (w === win) setTimeout(s.fitNow, 30);
    });

    // virtual keyboard toolbar (keyboard.js)
    if (OS.kb) OS.kb.attach(win, s);

    OS.winClose.terminal = function (w) {
      clearInterval(latTimer);
      attUnsub();
      // window close terminates the session (single-user semantics)
      var sess = term.sessions.get(sid);
      if (sess) {
        term.sessions.delete(sid);
        OS.socket.emit('terminal_close', {sid: sid});
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

  function pasteInto(sid) {
    function send(text) { if (text) term.sendInput(sid, text); }
    if (navigator.clipboard && navigator.clipboard.readText) {
      navigator.clipboard.readText().then(send).catch(function () {
        OS.toast('Clipboard unavailable', true);
      });
    } else {
      OS.toast('Clipboard unavailable', true);
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
