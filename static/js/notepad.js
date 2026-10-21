/* notepad.js — scratchpad with cross-terminal paste (IDEA.md §6.4).
   Content persists in localStorage; desktop/tablet drag onto a terminal
   window sends it; phone uses Send to Terminal with a session picker;
   terminal selections feed the store automatically. */
(function () {
  'use strict';
  var OS = window.OS;
  var $ = OS.$;

  OS.winInit.notepad = function (win) {
    var ta = $('.notepad-text', win.body);
    ta.value = OS.store.get('notepad', '');
    var saveTimer = null;
    ta.addEventListener('input', function () {
      clearTimeout(saveTimer);
      saveTimer = setTimeout(function () {
        OS.store.set('notepad', ta.value);
      }, 300);
    });

    // ingest terminal selections (set from terminal.js via bus)
    var unsub = OS.bus.on('selection', function (data) {
      if (!data.text) return;
      var cur = OS.store.get('notepad', '');
      var next = cur + (cur && !cur.endsWith('\n') ? '\n' : '') + data.text + '\n';
      if (ta.value !== next) {
        ta.value = next;
        OS.store.set('notepad', next);
      }
    });
    OS.winClose.notepad = function () { unsub(); };

    // drag handle → terminal drop (handled by terminal.js drop listener)
    var handle = $('.notepad-drag', win.body);
    handle.addEventListener('dragstart', function (e) {
      e.dataTransfer.setData('text/plain', ta.value);
      e.dataTransfer.effectAllowed = 'copy';
    });

    // toolbar buttons
    var toolbar = $('.notepad-toolbar', win.body);
    toolbar.addEventListener('click', function (e) {
      var btn = e.target.closest('button');
      if (!btn) return;
      var act = btn.dataset.act;
      if (act === 'copy') {
        if (navigator.clipboard) {
          navigator.clipboard.writeText(ta.value).then(function () {
            OS.toast(OS.t('copied'));
          });
        }
      } else if (act === 'clear') {
        ta.value = '';
        OS.store.set('notepad', '');
      } else if (act === 'send') {
        if (!ta.value) return;
        sendDialog(ta.value);
      }
    });

    function sendDialog(text) {
      var sessions = [];
      OS.term.sessions.forEach(function (s) {
        if (s.state !== 'closed') sessions.push(s);
      });
      var body = OS.el('div');
      if (!sessions.length) {
        body.appendChild(OS.el('p', '', OS.t('no_sessions')));
      }
      sessions.forEach(function (s) {
        var btn = OS.el('button', 'menu-item');
        btn.appendChild(OS.el('span', 'mi-icon', '>_'));
        btn.appendChild(OS.el('span', '', s.hostName));
        btn.addEventListener('click', function () {
          var withNl = OS.store.get('notepad-send-newline', true);
          OS.term.sendInput(s.sid, text + (withNl ? '\n' : ''));
          modal.close();
          OS.toast(s.hostName + ' ← ' + text.length + ' chars');
        });
        body.appendChild(btn);
      });
      var nl = OS.el('label', '', '');
      nl.style.cssText = 'display:flex;gap:8px;align-items:center;margin-top:10px;font-size:13px';
      var cb = OS.el('input');
      cb.type = 'checkbox';
      cb.checked = OS.store.get('notepad-send-newline', true);
      cb.addEventListener('change', function () {
        OS.store.set('notepad-send-newline', cb.checked);
      });
      nl.appendChild(cb);
      nl.appendChild(document.createTextNode(OS.t('send_newline')));
      body.appendChild(nl);
      var modal = OS.modal({
        title: OS.t('send_to_terminal'),
        body: body,
        buttons: [{label: OS.t('cancel')}]
      });
    }
  };
})();
