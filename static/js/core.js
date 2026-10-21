/* core.js — shared plumbing: event bus, API wrapper, storage, modal,
   confirm, toast, context menu, formatting helpers. */
(function () {
  'use strict';
  var OS = window.OS = window.OS || {};

  OS.t = window.t;

  // ---- tiny event bus ----------------------------------------------------
  var listeners = {};
  OS.bus = {
    on: function (ev, fn) {
      (listeners[ev] = listeners[ev] || []).push(fn);
      return function () { OS.bus.off(ev, fn); };
    },
    off: function (ev, fn) {
      var arr = listeners[ev] || [];
      var i = arr.indexOf(fn);
      if (i >= 0) arr.splice(i, 1);
    },
    emit: function (ev, data) {
      (listeners[ev] || []).slice().forEach(function (fn) {
        try { fn(data); } catch (e) { console.error('bus handler', ev, e); }
      });
    }
  };

  // ---- API ----------------------------------------------------------------
  OS.api = function (path, opts) {
    opts = opts || {};
    opts.headers = Object.assign({'Content-Type': 'application/json'}, opts.headers || {});
    if (opts.body && typeof opts.body !== 'string') opts.body = JSON.stringify(opts.body);
    return fetch(path, opts).then(function (r) {
      if (r.status === 401) {
        window.location.href = '/auth/login';
        throw new Error('unauthorized');
      }
      return r.json().catch(function () { return {}; });
    });
  };

  // ---- localStorage ---------------------------------------------------------
  OS.store = {
    get: function (key, dflt) {
      try {
        var v = localStorage.getItem('os-' + key);
        return v === null ? dflt : JSON.parse(v);
      } catch (e) { return dflt; }
    },
    set: function (key, value) {
      try { localStorage.setItem('os-' + key, JSON.stringify(value)); } catch (e) {}
    }
  };

  // ---- DOM helpers ------------------------------------------------------------
  OS.$ = function (sel, root) { return (root || document).querySelector(sel); };
  OS.$$ = function (sel, root) {
    return Array.prototype.slice.call((root || document).querySelectorAll(sel));
  };
  OS.el = function (tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  };
  OS.escapeHtml = function (s) {
    return String(s === null || s === undefined ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  };

  // ---- modal ---------------------------------------------------------------------
  OS.modal = function (opts) {
    var root = OS.$('#modal-root');
    var backdrop = OS.el('div', 'modal-backdrop');
    var modal = OS.el('div', 'modal');
    var head = OS.el('div', 'modal-head');
    head.appendChild(OS.el('span', '', opts.title || ''));
    var closeBtn = OS.el('button', 'win-btn close', '✕');
    head.appendChild(closeBtn);
    var body = OS.el('div', 'modal-body');
    if (typeof opts.body === 'string') body.innerHTML = opts.body;
    else if (opts.body) body.appendChild(opts.body);
    modal.appendChild(head);
    modal.appendChild(body);
    if (opts.buttons && opts.buttons.length) {
      var foot = OS.el('div', 'modal-foot');
      opts.buttons.forEach(function (b) {
        var btn = OS.el('button', 'btn btn-sm ' + (b.cls || ''), b.label);
        btn.addEventListener('click', function () {
          if (!b.onClick || b.onClick(api) !== false) api.close();
        });
        foot.appendChild(btn);
      });
      modal.appendChild(foot);
    }
    backdrop.appendChild(modal);
    root.appendChild(backdrop);
    function onClose() { if (opts.onClose) opts.onClose(); }
    var api = {
      el: modal, body: body,
      close: function () { backdrop.remove(); onClose(); }
    };
    closeBtn.addEventListener('click', api.close);
    backdrop.addEventListener('click', function (e) {
      if (e.target === backdrop) api.close();
    });
    return api;
  };

  OS.confirm = function (message, danger) {
    return new Promise(function (resolve) {
      OS.modal({
        title: OS.t('confirm'),
        body: OS.el('p', '', message),
        buttons: [
          {label: OS.t('cancel'), onClick: function () { resolve(false); }},
          {label: OS.t('confirm'), cls: danger ? 'btn-danger' : 'btn-primary',
           onClick: function () { resolve(true); }}
        ]
      });
    });
  };

  OS.prompt = function (label, value) {
    return new Promise(function (resolve) {
      var input = OS.el('input');
      input.value = value || '';
      var m = OS.modal({
        title: label,
        body: input,
        buttons: [
          {label: OS.t('cancel'), onClick: function () { resolve(null); }},
          {label: OS.t('ok'), cls: 'btn-primary', onClick: function () { resolve(input.value); }}
        ],
        onClose: function () { resolve(null); }
      });
      setTimeout(function () { input.focus(); }, 50);
      input.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') { resolve(input.value); m.close(); }
      });
    });
  };

  // ---- toast -------------------------------------------------------------------------
  OS.toast = function (msg, isError) {
    var root = OS.$('#toast-root');
    var tEl = OS.el('div', 'toast' + (isError ? ' error' : ''), String(msg));
    root.appendChild(tEl);
    setTimeout(function () { tEl.remove(); }, 3500);
  };

  // ---- context menu ---------------------------------------------------------------------
  var ctxEl = null;
  OS.ctxMenu = function (items, x, y) {
    OS.ctxMenu.close();
    ctxEl = OS.el('div', '');
    ctxEl.id = 'ctx-menu';
    items.forEach(function (item) {
      if (item === '-') { ctxEl.appendChild(OS.el('div', 'menu-sep')); return; }
      var btn = OS.el('button', 'menu-item' + (item.danger ? ' danger' : ''));
      var ico = OS.el('span', 'mi-icon', item.icon || '');
      btn.appendChild(ico);
      btn.appendChild(OS.el('span', '', item.label));
      btn.addEventListener('click', function () {
        OS.ctxMenu.close();
        item.onClick();
      });
      ctxEl.appendChild(btn);
    });
    document.body.appendChild(ctxEl);
    ctxEl.hidden = false;
    var rect = ctxEl.getBoundingClientRect();
    var px = Math.min(x, window.innerWidth - rect.width - 8);
    var py = Math.min(y, window.innerHeight - rect.height - 8);
    ctxEl.style.left = Math.max(4, px) + 'px';
    ctxEl.style.top = Math.max(4, py) + 'px';
    return ctxEl;
  };
  OS.ctxMenu.close = function () {
    var el = document.getElementById('ctx-menu');
    if (el) el.remove();
    ctxEl = null;
  };
  document.addEventListener('click', function (e) {
    if (ctxEl && !ctxEl.contains(e.target)) OS.ctxMenu.close();
  });
  document.addEventListener('contextmenu', function (e) {
    // close any open menu; specific menus re-open on their own handler
    if (ctxEl && !ctxEl.contains(e.target)) OS.ctxMenu.close();
  });

  // ---- formatting ----------------------------------------------------------------------------
  OS.fmtSize = function (n) {
    if (n === null || n === undefined) return '';
    if (n < 1024) return n + ' B';
    var units = ['KB', 'MB', 'GB', 'TB'];
    var i = -1;
    do { n /= 1024; i++; } while (n >= 1024 && i < units.length - 1);
    return n.toFixed(n >= 100 ? 0 : 1) + ' ' + units[i];
  };
  function pad(n) { return (n < 10 ? '0' : '') + n; }
  OS.fmtDateTime = function (ts) {
    if (!ts) return '';
    var d = new Date(ts * 1000);
    return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) +
      ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
  };
  OS.relTime = function (ts) {
    if (!ts) return '';
    var diff = Math.floor(Date.now() / 1000 - ts);
    if (diff < 60) return diff + ' seconds ago';
    if (diff < 3600) {
      var m = Math.floor(diff / 60);
      return m + ' minute' + (m > 1 ? 's' : '') + ' ago';
    }
    if (diff < 86400) {
      var h = Math.floor(diff / 3600);
      return h + ' hour' + (h > 1 ? 's' : '') + ' ago';
    }
    var dd = Math.floor(diff / 86400);
    return dd + ' day' + (dd > 1 ? 's' : '') + ' ago';
  };
})();
