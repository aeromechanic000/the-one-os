/* keyboard.js — layered virtual keyboard (IDEA.md §5).
   Layer 1: persistent toolbar of terminal-essential keys.
   Layer 2: expandable full keyboard (letters, numbers, symbols, Fn, combos).
   Layer 3: user custom keys from settings, appended to the toolbar.
   Sticky modifiers: tap to press, tap again to release, double-tap to lock. */
(function () {
  'use strict';
  var OS = window.OS;
  var $ = OS.$;

  var CTRL_CHARS = {};
  for (var c = 1; c <= 26; c++) CTRL_CHARS[String.fromCharCode(96 + c)] =
    String.fromCharCode(c);

  var SEQ = {
    esc: '\x1b', tab: '\t', enter: '\r', backspace: '\x7f',
    up: '\x1b[A', down: '\x1b[B', right: '\x1b[C', left: '\x1b[D',
    home: '\x1b[H', end: '\x1b[F', del: '\x1b[3~', pgup: '\x1b[5~',
    pgdn: '\x1b[6~'
  };

  var kb = OS.kb = {
    settings: {
      mode: 'windows',        // windows | mac
      strategy: 'coexist',    // coexist | replace
      customKeys: []
    },
    sticky: {ctrl: false, alt: false, shift: false},
    locked: {ctrl: false, alt: false, shift: false},
    _lastModTap: {},
    panel: null,

    loadSettings: function () {
      return OS.api('/api/settings').then(function (s) {
        kb.settings.mode = s.keyboard_mode || 'windows';
        kb.settings.strategy = s.keyboard_strategy || 'coexist';
        kb.settings.customKeys = s.custom_keys || [];
        OS.store.set('terminal-font-size', s.terminal_font_size || 14);
        kb.applyAllTerminals();
      });
    },

    saveSettings: function (patch) {
      Object.assign(kb.settings, patch);
      return OS.api('/api/settings', {method: 'PUT', body: patch})
        .then(function () { kb.applyAllTerminals(); });
    },

    // ---- key sequence parsing (\x1b, \x03, \n escapes in custom keys) ----
    parseSequence: function (seq) {
      return String(seq).replace(/\\x([0-9a-fA-F]{2})/g, function (_, h) {
        return String.fromCharCode(parseInt(h, 16));
      }).replace(/\\e/g, '\x1b').replace(/\\n/g, '\n').replace(/\\r/g, '\r')
        .replace(/\\t/g, '\t');
    },

    // ---- attach to a terminal window ------------------------------------
    attach: function (win, session) {
      var toolbar = $('.vk-toolbar', win.body);
      if (!toolbar) return;
      toolbar.innerHTML = '';
      kb.buildToolbar(toolbar, session);
      kb.applyInputMode(session);
    },

    applyAllTerminals: function () {
      if (!OS.term) return;
      OS.term.sessions.forEach(function (s) {
        if (s.win && s.win.el && s.win.el.isConnected) {
          kb.attach(s.win, s);
          kb.applyInputMode(s);
        }
      });
    },

    applyInputMode: function (session) {
      if (!session.term || !session.term.textarea) return;
      var ta = session.term.textarea;
      if (kb.settings.strategy === 'replace') {
        ta.setAttribute('inputmode', 'none');
        ta.setAttribute('readonly', 'readonly');
      } else {
        ta.removeAttribute('inputmode');
        ta.removeAttribute('readonly');
      }
    },

    // ---- toolbar (layer 1 + layer 3) ---------------------------------------
    buildToolbar: function (toolbar, session) {
      var mac = kb.settings.mode === 'mac';
      toolbar.innerHTML = '';

      function key(label, seq, cls, title) {
        var btn = OS.el('button', 'vk-key ' + (cls || ''), label);
        if (title) btn.title = title;
        btn.addEventListener('pointerdown', function (e) {
          e.preventDefault();       // keep terminal focus
          kb.sendKey(session, seq, btn);
        });
        toolbar.appendChild(btn);
        return btn;
      }

      // sticky modifiers (Ctrl / Option-Alt)
      [
        {mod: 'ctrl', label: 'Ctrl'},
        {mod: 'alt', label: mac ? 'Option' : 'Alt'}
      ].forEach(function (m) {
        var btn = OS.el('button', 'vk-key mod', m.label);
        btn.title = OS.t('kbk_sticky');
        btn.dataset.mod = m.mod;
        btn.addEventListener('pointerdown', function (e) {
          e.preventDefault();
          kb.tapModifier(session, m.mod, btn);
        });
        toolbar.appendChild(btn);
        kb._refreshModBtn(m.mod, btn);
      });

      [
        ['Esc', SEQ.esc], ['Tab', SEQ.tab],
        ['↑', SEQ.up], ['↓', SEQ.down], ['←', SEQ.left], ['→', SEQ.right],
        ['|', '|'], ['~', '~'], ['/', '/'], ['-', '-'], ['_', '_'],
        [':', ':'], ['"', '"'], ["'", "'"]
      ].forEach(function (k) {
        key(k[0], k[1]);
      });

      // layer 3: custom keys
      kb.settings.customKeys.forEach(function (ck) {
        var btn = OS.el('button', 'vk-key custom-key', ck.label);
        btn.title = ck.sequence;
        btn.addEventListener('pointerdown', function (e) {
          e.preventDefault();
          kb.send(session, kb.parseSequence(ck.sequence));
        });
        toolbar.appendChild(btn);
      });

      // keyboard strategy toggle (coexist/replace)
      var kbBtn = OS.el('button', 'vk-key', '⌨');
      kbBtn.title = OS.t('kb_strategy') + ': ' + kb.settings.strategy;
      kbBtn.addEventListener('pointerdown', function (e) {
        e.preventDefault();
        var next = kb.settings.strategy === 'coexist' ? 'replace' : 'coexist';
        kb.saveSettings({keyboard_strategy: next}).then(function () {
          OS.toast(OS.t('kb_strategy') + ': ' + next);
        });
      });
      toolbar.appendChild(kbBtn);

      var exp = OS.el('button', 'vk-key vk-expand', '⤢ ' + OS.t('kbk_expand'));
      exp.addEventListener('pointerdown', function (e) {
        e.preventDefault();
        kb.togglePanel(session);
      });
      toolbar.appendChild(exp);
    },

    // ---- full panel (layer 2) -------------------------------------------------
    togglePanel: function (session) {
      if (kb.panel && kb.panel.isConnected) {
        kb.panel.remove();
        kb.panel = null;
        if (session && session.fitNow) session.fitNow();
        return;
      }
      var win = session.win;
      var panel = OS.el('div', 'vk-panel');
      kb.panel = panel;
      var mac = kb.settings.mode === 'mac';

      var head = OS.el('div', 'vk-panel-head');
      head.appendChild(OS.el('span', '', OS.t('kbk_sticky')));
      var close = OS.el('button', 'vk-key vk-close', OS.t('kbk_collapse'));
      close.addEventListener('pointerdown', function (e) {
        e.preventDefault();
        kb.togglePanel(session);
      });
      head.appendChild(close);
      panel.appendChild(head);

      // numbers
      panel.appendChild(kb._row(session, '1234567890'.split('')));
      // letters
      ['qwertyuiop', 'asdfghjkl', 'zxcvbnm'].forEach(function (rowKeys, i) {
        var row = OS.el('div', 'vk-row');
        if (i === 2) {
          row.appendChild(kb._mk(session, 'Shift', 'mod:shift', 'mod'));
        }
        rowKeys.split('').forEach(function (ch) {
          row.appendChild(kb._mk(session, ch, ch));
        });
        if (i === 2) {
          row.appendChild(kb._mk(session, '⌫', SEQ.backspace, 'w2'));
        }
        panel.appendChild(row);
      });
      // modifiers + space + enter row
      var row = OS.el('div', 'vk-row');
      row.appendChild(kb._mk(session, 'Ctrl', 'mod:ctrl', 'mod'));
      row.appendChild(kb._mk(session, mac ? 'Option' : 'Alt', 'mod:alt', 'mod'));
      row.appendChild(kb._mk(session, mac ? 'Cmd⌘' : 'Win', 'mod:meta', 'mod'));
      row.appendChild(kb._mk(session, ' ', ' ', 'w2'));
      row.appendChild(kb._mk(session, 'Esc', SEQ.esc));
      row.appendChild(kb._mk(session, 'Tab', SEQ.tab));
      row.appendChild(kb._mk(session, '⏎', SEQ.enter, 'w2'));
      panel.appendChild(row);
      // arrows row
      var arrows = OS.el('div', 'vk-row');
      arrows.appendChild(kb._mk(session, '←', SEQ.left));
      arrows.appendChild(kb._mk(session, '↑', SEQ.up));
      arrows.appendChild(kb._mk(session, '↓', SEQ.down));
      arrows.appendChild(kb._mk(session, '→', SEQ.right));
      arrows.appendChild(kb._mk(session, 'Home', SEQ.home));
      arrows.appendChild(kb._mk(session, 'End', SEQ.end));
      panel.appendChild(arrows);
      // symbols
      panel.appendChild(kb._row(session,
        '!@#$%^&*()_+-=[]{}\\|;:\'",.<>/?`'.split('')));
      // Fn row
      var fn = OS.el('div', 'vk-fn-row');
      for (var f = 1; f <= 12; f++) {
        fn.appendChild(kb._mk(session, 'F' + f, kb._fnSeq(f)));
      }
      panel.appendChild(fn);
      // combos
      var combos = OS.el('div', 'vk-combos');
      var comboDefs = mac ? [
        ['Cmd+C', 'clip:copy'], ['Cmd+V', 'clip:paste'],
        ['Ctrl+C', CTRL_CHARS.c], ['Ctrl+D', CTRL_CHARS.d],
        ['Ctrl+Z', CTRL_CHARS.z], ['Ctrl+L', CTRL_CHARS.l],
        ['Ctrl+A', CTRL_CHARS.a], ['Ctrl+E', CTRL_CHARS.e],
        ['Ctrl+U', CTRL_CHARS.u], ['Ctrl+K', CTRL_CHARS.k],
        ['Ctrl+R', CTRL_CHARS.r]
      ] : [
        ['Ctrl+C', CTRL_CHARS.c], ['Ctrl+D', CTRL_CHARS.d],
        ['Ctrl+Z', CTRL_CHARS.z], ['Ctrl+L', CTRL_CHARS.l],
        ['Ctrl+A', CTRL_CHARS.a], ['Ctrl+E', CTRL_CHARS.e],
        ['Ctrl+U', CTRL_CHARS.u], ['Ctrl+K', CTRL_CHARS.k],
        ['Ctrl+R', CTRL_CHARS.r]
      ];
      comboDefs.forEach(function (cd) {
        var btn = OS.el('button', 'vk-key', cd[0]);
        btn.addEventListener('pointerdown', function (e) {
          e.preventDefault();
          if (cd[1] === 'clip:copy') kb.clipCopy(session);
          else if (cd[1] === 'clip:paste') kb.clipPaste(session);
          else kb.send(session, cd[1]);
        });
        combos.appendChild(btn);
      });
      panel.appendChild(combos);

      $('.term-wrap', win.body).appendChild(panel);
      if (session.fitNow) setTimeout(session.fitNow, 60);
    },

    _row: function (session, chars) {
      var row = OS.el('div', 'vk-row');
      chars.forEach(function (ch) { row.appendChild(kb._mk(session, ch, ch)); });
      return row;
    },

    _mk: function (session, label, seq, cls) {
      var btn = OS.el('button', 'vk-key ' + (cls || ''), label);
      btn.addEventListener('pointerdown', function (e) {
        e.preventDefault();
        if (seq.indexOf('mod:') === 0) {
          kb.tapModifier(session, seq.slice(4), btn);
          return;
        }
        kb.sendKey(session, seq, btn);
      });
      return btn;
    },

    // ---- modifier state machine ------------------------------------------------
    tapModifier: function (session, mod, btn) {
      var now = Date.now();
      var last = kb._lastModTap[mod] || 0;
      kb._lastModTap[mod] = now;
      if (kb.sticky[mod] && (now - last) < 350) {
        // double-tap locks
        kb.locked[mod] = !kb.locked[mod];
        kb.sticky[mod] = kb.locked[mod];
      } else if (kb.sticky[mod] && !kb.locked[mod]) {
        kb.sticky[mod] = false;
      } else {
        kb.sticky[mod] = true;
      }
      kb._refreshMods(session);
      kb.showModHint(session);
    },

    _refreshMods: function (session) {
      document.querySelectorAll('.vk-key[data-mod]').forEach(function (btn) {
        kb._refreshModBtn(btn.dataset.mod, btn);
      });
    },

    _refreshModBtn: function (mod, btn) {
      btn.classList.toggle('pressed', kb.sticky[mod] && !kb.locked[mod]);
      btn.classList.toggle('locked', !!kb.locked[mod]);
    },

    showModHint: function (session) {
      if (!session || !session.win || !session.win.body) return;
      var old = $('.vk-mod-hint', session.win.body);
      if (old) old.remove();
      var active = Object.keys(kb.sticky).filter(function (m) {
        return kb.sticky[m];
      });
      if (!active.length) return;
      var label = {ctrl: 'Ctrl', alt: 'Alt', shift: 'Shift'}[active[0]];
      var hint = OS.el('div', 'vk-mod-hint',
        OS.t('kbk_modifier_on', {key: label}));
      $('.term-wrap', session.win.body).appendChild(hint);
      clearTimeout(kb._hintTimer);
      kb._hintTimer = setTimeout(function () { hint.remove(); }, 1600);
    },

    // ---- sending ---------------------------------------------------------------
    send: function (session, text) {
      OS.term.sendInput(session.sid, text);
    },

    sendKey: function (session, seq, btn) {
      var appliedCtrl = false, appliedAlt = false, appliedShift = false;
      if (kb.sticky.ctrl && CTRL_CHARS[seq.toLowerCase()] !== undefined &&
          seq.length === 1) {
        seq = CTRL_CHARS[seq.toLowerCase()];
        appliedCtrl = true;
      } else if (kb.sticky.ctrl && seq.length === 1) {
        // Ctrl with non-letter: send anyway, consume modifier
        appliedCtrl = true;
      }
      if (kb.sticky.alt && seq.length === 1) {
        seq = '\x1b' + seq;
        appliedAlt = true;
      }
      if (kb.sticky.shift && seq.length === 1) {
        var flip = seq.toLowerCase() !== seq ? seq.toLowerCase() : seq.toUpperCase();
        seq = flip;
        appliedShift = true;
      }
      kb.send(session, seq);
      // release non-locked modifiers after a key
      ['ctrl', 'alt', 'shift'].forEach(function (m) {
        if (kb.sticky[m] && !kb.locked[m]) {
          kb.sticky[m] = false;
        } else if (kb.locked[m] && appliedCtrl + appliedAlt + appliedShift) {
          // locks persist
        }
      });
      kb._refreshMods(session);
    },

    // ---- clipboard combos (mac-mode display mapping) -----------------------------
    clipCopy: function (session) {
      var sel = session.term ? session.term.getSelection() : '';
      if (sel && navigator.clipboard) {
        navigator.clipboard.writeText(sel).catch(function () {});
      }
      OS.bus.emit('selection', {sid: session.sid, text: sel});
      OS.toast(OS.t('copied'));
    },

    clipPaste: function (session) {
      if (navigator.clipboard && navigator.clipboard.readText) {
        navigator.clipboard.readText().then(function (text) {
          if (text) kb.send(session, text);
        }).catch(function () { OS.toast('Clipboard unavailable', true); });
      }
    },

    // ---- hardware keyboard detection (IDEA.md §5.6) --------------------------------
    initHardwareDetection: function () {
      document.addEventListener('keydown', function (e) {
        // virtual keys are pointer events; a real keydown means hardware
        if (e.isTrusted && kb.panel && kb.panel.isConnected &&
            OS.layout.isTouch) {
          kb.panel.remove();
          kb.panel = null;
          OS.term.sessions.forEach(function (s) {
            if (s.fitNow) s.fitNow();
          });
        }
      });
    }
  };

  // Fn key sequences: F1-F4 are SS3 P-S, F5+ are CSI 15~ and up.
  kb._fnSeq = function (f) {
    var ss3 = {1: 'P', 2: 'Q', 3: 'R', 4: 'S'};
    if (ss3[f]) return '\x1bO' + ss3[f];
    return '\x1b[' + (11 + f) + '~';   // F5 -> \x1b[15~ ... F12 -> \x1b[24~
  };
})();
