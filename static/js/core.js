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

  // ---- inline SVG icons (never emoji) ---------------------------------------
  // 16x16 stroke icons, currentColor so they follow the text/accent color.
  var ICON_PATHS = {
    close: '<path d="M4 4l8 8M12 4l-8 8"/>',
    min: '<path d="M3.5 8.5h9"/>',
    max: '<rect x="3.5" y="3.5" width="9" height="9" rx="1"/>',
    back: '<path d="M10 3.5 5.5 8l4.5 4.5"/>',
    chevron: '<path d="M6 3.5 10.5 8 6 12.5"/>',
    terminal: '<rect x="2" y="3" width="12" height="10" rx="1.5"/>' +
      '<path d="M5 7l2.5 2.5L5 12M9.5 12H12"/>',
    edit: '<path d="M3.5 12.5l.7-2.6 6.6-6.6 1.9 1.9-6.6 6.6-2.6.7z"/>' +
      '<path d="M10 4.5l1.5 1.5"/>',
    folder: '<path d="M2 4.5c0-.6.4-1 1-1h3l1.5 2H13c.6 0 1 .4 1 1v6' +
      'c0 .6-.4 1-1 1H3c-.6 0-1-.4-1-1z"/>',
    bolt: '<path d="M9.5 1.5 3.5 9.5h3.5L7 14.5l6-8H9.5z"/>',
    server: '<rect x="2.5" y="2.5" width="11" height="4.5" rx="1"/>' +
      '<rect x="2.5" y="9" width="11" height="4.5" rx="1"/>' +
      '<path d="M5 4.75h.01M5 11.25h.01"/>',
    gear: '<circle cx="8" cy="8" r="2.4"/>' +
      '<path d="M8 1.8v2.1M8 12.1v2.1M1.8 8h2.1M12.1 8h2.1M3.6 3.6l1.5 1.5' +
      'M10.9 10.9l1.5 1.5M12.4 3.6l-1.5 1.5M5.1 10.9l-1.5 1.5"/>',
    eye: '<path d="M1.5 8S4 3.5 8 3.5 14.5 8 14.5 8 12 12.5 8 12.5' +
      ' 1.5 8 1.5 8z"/><circle cx="8" cy="8" r="2"/>',
    image: '<rect x="2" y="2.5" width="12" height="11" rx="1.5"/>' +
      '<circle cx="5.5" cy="6" r="1.1"/>' +
      '<path d="M2 11l3.5-3 3 2.5L11 8l3 3"/>',
    film: '<rect x="2" y="3" width="12" height="10" rx="1.5"/>' +
      '<path d="M5 3v10M11 3v10M2 5.5h3M2 10.5h3M11 5.5h3M11 10.5h3"/>',
    music: '<path d="M6.5 12V4.2l7-1.2v7.5"/>' +
      '<circle cx="4.3" cy="12.2" r="2.2"/><circle cx="11.3" cy="10.5" r="2.2"/>',
    doc: '<path d="M4 1.5h5.5L13 5v9.5H4z"/><path d="M9.5 1.5V5H13"/>',
    docText: '<path d="M4 1.5h5.5L13 5v9.5H4z"/><path d="M9.5 1.5V5H13"/>' +
      '<path d="M6.5 8h3.5M6.5 10.5h3.5"/>',
    code: '<path d="M6 4.5 2.5 8 6 11.5M10 4.5 13.5 8 10 11.5"/>',
    archive: '<path d="M2.5 2.5h11v3h-11z"/><path d="M3.5 5.5v8h9v-8"/>' +
      '<path d="M6.5 8.5h3"/>',
    table: '<rect x="2" y="2.5" width="12" height="11" rx="1.5"/>' +
      '<path d="M2 6h12M2 9.75h12M7 6v7.5"/>',
    download: '<path d="M8 2v8M4.5 6.8 8 10.3l3.5-3.5"/>' +
      '<path d="M2.5 13.5h11"/>',
    send: '<path d="M2.5 8h10M9 4.5 12.5 8 9 11.5"/>',
    up: '<path d="M8 13V5M4.5 8.5 8 5l3.5 3.5"/>',
    trash: '<path d="M3 4.5h10M6 4.5V3h4v1.5M4.5 4.5l.8 9h5.4l.8-9"/>' +
      '<path d="M6.8 7.5v3.5M9.2 7.5v3.5"/>',
    sparkle: '<path d="M8 1.5l1.5 3.9 3.9 1.5-3.9 1.5L8 12.4 6.5 8.4' +
      ' 2.6 6.9l3.9-1.5z"/><path d="M13 11l.6 1.6 1.6.4-1.6.4L13 15' +
      'l-.4-1.6L11 13l1.6-.4z"/>',
    logout: '<path d="M6.5 2.5h-3v11h3"/><path d="M9 8h5M11.5 5.5 14 8l-2.5 2.5"/>',
    play: '<path d="M5 3.5v9l7.5-4.5z"/>',
    diamond: '<path d="M8 1.5 14.5 8 8 14.5 1.5 8z"/>',
    diamondFill: '<path d="M8 1.5 14.5 8 8 14.5 1.5 8z" fill="currentColor" stroke="none"/>'
  };
  var ICON_SVG = {};
  Object.keys(ICON_PATHS).forEach(function (name) {
    ICON_SVG[name] =
      '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" ' +
      'stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" ' +
      'aria-hidden="true">' + ICON_PATHS[name] + '</svg>';
  });
  OS.iconSvg = function (name) { return ICON_SVG[name] || ''; };
  OS.icon = function (name, cls) {
    var span = document.createElement('span');
    span.className = 'ico' + (cls ? ' ' + cls : '');
    span.innerHTML = OS.iconSvg(name);
    return span;
  };

  // ---- modal ---------------------------------------------------------------------
  OS.modal = function (opts) {
    var root = OS.$('#modal-root');
    var backdrop = OS.el('div', 'modal-backdrop');
    var modal = OS.el('div', 'modal');
    var head = OS.el('div', 'modal-head');
    head.appendChild(OS.el('span', '', opts.title || ''));
    var closeBtn = OS.el('button', 'win-btn close');
    closeBtn.appendChild(OS.icon('close'));
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
      var ico = OS.el('span', 'mi-icon');
      ico.innerHTML = (item.icon && item.icon.indexOf('<svg') === 0)
        ? item.icon : OS.iconSvg(item.icon || '');
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
