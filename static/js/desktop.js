/* desktop.js — window manager, desktop icons, taskbar, start menu, phone
   host list + bottom nav, top bar status, Host Manager UI (IDEA.md §8). */
(function () {
  'use strict';
  var OS = window.OS;
  var $ = OS.$, $$ = OS.$$;

  OS.state = {
    hosts: [],                 // host records from /api/hosts
    status: {},                // host_id -> true/false/undefined
    attention: {},             // sid -> {name, confidence, hint}
    session: null
  };

  // Window init hooks, registered by feature modules: OS.winInit[type](win)
  OS.winInit = {};
  OS.winClose = {};

  var WIN_ICONS = {
    terminal: 'terminal', notepad: 'edit', files: 'folder', actions: 'bolt',
    hosts: 'server', settings: 'gear', preview: 'eye'
  };
  var SINGLETONS = {notepad: 1, files: 1, actions: 1, hosts: 1, settings: 1, preview: 1};

  // ================= window manager =================
  var wm = OS.wm = {
    windows: new Map(),
    _z: 100,
    _seq: 0,

    init: function () {
      // blank desktop menu
      OS.$('#desktop-layer').addEventListener('contextmenu', function (e) {
        if (e.target.closest('.window') || e.target.closest('.desktop-icon') ||
            e.target.closest('#phone-home')) return;
        e.preventDefault();
        OS.ctxMenu([
          {icon: 'edit', label: OS.t('ctx_new_note'),
           onClick: function () { wm.openApp('notepad'); }},
          {icon: 'folder', label: OS.t('ctx_file_transfer'),
           onClick: function () { wm.openApp('files'); }},
          {icon: 'bolt', label: OS.t('ctx_quick_actions'),
           onClick: function () { wm.openApp('actions'); }},
          {icon: 'server', label: OS.t('ctx_host_manager'),
           onClick: function () { wm.openApp('hosts'); }},
          {icon: 'gear', label: OS.t('ctx_system_settings'),
           onClick: function () { wm.openApp('settings'); }}
        ], e.clientX, e.clientY);
      });
      OS.layout.onChange(function () {
        wm.windows.forEach(function (win) {
          if (OS.layout.form !== 'desktop') {
            win.el.classList.add('maximized');
          }
          wm.relayoutPhone();
        });
        renderTabs();
      });
      this.initSwipeBack();
    },

    openApp: function (type, opts) {
      return wm.create(type, opts || {});
    },

    create: function (type, opts) {
      opts = opts || {};
      if (SINGLETONS[type]) {
        var existing = null;
        wm.windows.forEach(function (w) { if (w.type === type) existing = w; });
        if (existing) {
          existing.el.classList.remove('hidden-win');
          wm.focus(existing);
          if (opts.update) opts.update(existing);
          return existing;
        }
      }
      var tpl = document.getElementById('tpl-' + type);
      if (!tpl) return null;

      var win = {
        id: 'w' + (++wm._seq),
        type: type,
        opts: opts,
        el: OS.el('div', 'window')
      };
      var header = OS.el('div', 'win-header');
      var backBtn = OS.el('button', 'win-btn phone-back-btn');
      backBtn.title = OS.t('back');
      backBtn.appendChild(OS.icon('back'));
      var icon = OS.el('span', 'win-icon');
      icon.innerHTML = OS.iconSvg(WIN_ICONS[type] || '');
      var title = OS.el('span', 'win-title', opts.title || type);
      var badge = OS.el('span', 'win-attention-badge');
      badge.hidden = true;
      var minBtn = OS.el('button', 'win-btn');
      minBtn.appendChild(OS.icon('min'));
      var maxBtn = OS.el('button', 'win-btn');
      maxBtn.appendChild(OS.icon('max'));
      var closeBtn = OS.el('button', 'win-btn close');
      closeBtn.appendChild(OS.icon('close'));
      header.appendChild(backBtn);
      header.appendChild(icon);
      header.appendChild(title);
      header.appendChild(badge);
      header.appendChild(minBtn);
      header.appendChild(maxBtn);
      header.appendChild(closeBtn);
      var body = OS.el('div', 'win-body');
      body.appendChild(document.importNode(tpl.content, true));
      var handle = OS.el('div', 'resize-handle');
      win.el.appendChild(header);
      win.el.appendChild(body);
      win.el.appendChild(handle);
      win.body = body;
      win.titleEl = title;
      win.badgeEl = badge;

      win.setTitle = function (t) { title.textContent = t; renderTabs(); };
      win.setAttention = function (text, confidence) {
        badge.textContent = text;
        badge.classList.toggle('confidence-medium', confidence === 'medium');
        badge.hidden = !text;
        renderTabs();
      };

      backBtn.addEventListener('click', function () { wm.phoneBack(); });
      minBtn.addEventListener('click', function () { wm.minimize(win); });
      maxBtn.addEventListener('click', function () { wm.toggleMax(win); });
      closeBtn.addEventListener('click', function () { wm.close(win); });
      header.addEventListener('dblclick', function (e) {
        if (e.target === title || e.target === icon) wm.toggleMax(win);
      });

      wm._place(win, opts);
      OS.$('#windows-layer').appendChild(win.el);
      wm.windows.set(win.id, win);

      // interactions
      makeDraggable(win, header);
      makeResizable(win, handle);
      win.el.addEventListener('pointerdown', function () { wm.focus(win); });

      wm.focus(win);
      wm.relayoutPhone();
      renderTabs();
      if (OS.winInit[type]) OS.winInit[type](win);
      return win;
    },

    _place: function (win) {
      var layer = OS.$('#windows-layer').getBoundingClientRect();
      var geom = OS.store.get('wm-geom-' + win.type,
        defaultGeom(win.type, layer));
      if (OS.layout.form === 'phone') {
        win.el.classList.add('maximized');
        return;
      }
      if (OS.layout.form === 'tablet') {
        win.el.classList.add('maximized');
        return;
      }
      win.el.style.left = clamp(geom.left, 0, Math.max(0, layer.width - 200)) + 'px';
      win.el.style.top = clamp(geom.top, 0, Math.max(0, layer.height - 60)) + 'px';
      win.el.style.width = Math.min(geom.width, layer.width - 20) + 'px';
      win.el.style.height = Math.min(geom.height, layer.height - 20) + 'px';
      if (geom.maximized) win.el.classList.add('maximized');
    },

    focus: function (win) {
      win.el.style.zIndex = ++wm._z;
      wm._active = win;
      renderTabs();
    },

    minimize: function (win) {
      win.el.classList.add('hidden-win');
      wm.relayoutPhone();
      renderTabs();
    },

    toggleMax: function (win) {
      if (OS.layout.form === 'phone') return;
      win.el.classList.toggle('maximized');
      if (!win.el.classList.contains('maximized')) {
        win.el.style.left = win.el.style.left || '60px';
        win.el.style.top = win.el.style.top || '60px';
        win.el.style.width = win.el.style.width || '640px';
        win.el.style.height = win.el.style.height || '420px';
      }
      saveGeom(win);
      wm.fireResized(win);
    },

    close: function (win) {
      if (OS.winClose[win.type]) OS.winClose[win.type](win);
      win.el.remove();
      wm.windows.delete(win.id);
      if (wm._active === win) wm._active = null;
      saveGeom(win);
      wm.relayoutPhone();
      renderTabs();
    },

    closeByType: function (type) {
      var toClose = [];
      wm.windows.forEach(function (w) { if (w.type === type) toClose.push(w); });
      toClose.forEach(function (w) { wm.close(w); });
    },

    findBySid: function (sid) {
      var found = null;
      wm.windows.forEach(function (w) {
        if (w.opts && w.opts.sid === sid) found = w;
      });
      return found;
    },

    hasVisible: function () {
      var vis = false;
      wm.windows.forEach(function (w) {
        if (!w.el.classList.contains('hidden-win')) vis = true;
      });
      return vis;
    },

    relayoutPhone: function () {
      var phoneHome = OS.$('#phone-home');
      if (!phoneHome) return;
      if (OS.layout.form === 'phone') {
        phoneHome.hidden = wm.hasVisible();
      } else {
        phoneHome.hidden = true;
      }
    },

    phoneBack: function () {
      // hide the top-most visible window; session keeps running server-side
      var top = null;
      wm.windows.forEach(function (w) {
        if (w.el.classList.contains('hidden-win')) return;
        if (!top || +w.el.style.zIndex > +top.el.style.zIndex) top = w;
      });
      if (top) wm.minimize(top);
    },

    fireResized: function (win) {
      OS.bus.emit('win-resized', win);
    },

    // swipe left anywhere on a phone window goes back (IDEA.md §4.1)
    initSwipeBack: function () {
      var startX = null, startY = null;
      document.addEventListener('touchstart', function (e) {
        if (e.touches.length !== 1) { startX = null; return; }
        startX = e.touches[0].clientX;
        startY = e.touches[0].clientY;
      }, {passive: true});
      document.addEventListener('touchend', function (e) {
        if (startX === null || OS.layout.form !== 'phone') return;
        var dx = e.changedTouches[0].clientX - startX;
        var dy = e.changedTouches[0].clientY - startY;
        startX = null;
        if (dx < -70 && Math.abs(dy) < 50) wm.phoneBack();
      }, {passive: true});
    }
  };

  function defaultGeom(type, layer) {
    var presets = {
      terminal: {w: 720, h: 460},
      notepad: {w: 460, h: 380},
      files: {w: 780, h: 540},
      actions: {w: 640, h: 480},
      hosts: {w: 620, h: 420},
      settings: {w: 640, h: 520},
      preview: {w: 700, h: 520}
    };
    var p = presets[type] || {w: 600, h: 420};
    var n = wm.windows.size;
    return {
      left: Math.min(60 + n * 26, Math.max(10, layer.width - p.w - 10)),
      top: Math.min(40 + n * 22, Math.max(10, layer.height - p.h - 10)),
      width: Math.min(p.w, layer.width - 24),
      height: Math.min(p.h, layer.height - 24),
      maximized: false
    };
  }

  function saveGeom(win) {
    if (OS.layout.form !== 'desktop') return;
    if (!win.el.style.width) return;
    OS.store.set('wm-geom-' + win.type, {
      left: parseInt(win.el.style.left, 10) || 60,
      top: parseInt(win.el.style.top, 10) || 40,
      width: parseInt(win.el.style.width, 10) || 600,
      height: parseInt(win.el.style.height, 10) || 400,
      maximized: win.el.classList.contains('maximized')
    });
  }

  function clamp(v, lo, hi) { return Math.max(lo, Math.min(v, hi)); }

  function makeDraggable(win, header) {
    var sx = 0, sy = 0, ox = 0, oy = 0, dragging = false;
    header.addEventListener('pointerdown', function (e) {
      if (OS.layout.form === 'phone') return;
      if (e.target.closest('.win-btn')) return;
      if (win.el.classList.contains('maximized')) return;
      dragging = true;
      sx = e.clientX; sy = e.clientY;
      ox = parseInt(win.el.style.left, 10) || 0;
      oy = parseInt(win.el.style.top, 10) || 0;
      header.setPointerCapture(e.pointerId);
    });
    header.addEventListener('pointermove', function (e) {
      if (!dragging) return;
      var layer = OS.$('#windows-layer').getBoundingClientRect();
      win.el.style.left = clamp(ox + e.clientX - sx, -40, layer.width - 80) + 'px';
      win.el.style.top = clamp(oy + e.clientY - sy, 0, layer.height - 40) + 'px';
    });
    header.addEventListener('pointerup', function () {
      if (dragging) saveGeom(win);
      dragging = false;
    });
  }

  function makeResizable(win, handle) {
    var sx = 0, sy = 0, ow = 0, oh = 0, sizing = false;
    handle.addEventListener('pointerdown', function (e) {
      if (win.el.classList.contains('maximized')) return;
      sizing = true;
      sx = e.clientX; sy = e.clientY;
      ow = win.el.offsetWidth;
      oh = win.el.offsetHeight;
      handle.setPointerCapture(e.pointerId);
      e.preventDefault();
    });
    handle.addEventListener('pointermove', function (e) {
      if (!sizing) return;
      win.el.style.width = Math.max(320, ow + e.clientX - sx) + 'px';
      win.el.style.height = Math.max(200, oh + e.clientY - sy) + 'px';
    });
    handle.addEventListener('pointerup', function () {
      if (sizing) { saveGeom(win); wm.fireResized(win); }
      sizing = false;
    });
  }

  // ================= taskbar tabs / start menu / switcher =================
  function renderTabs() {
    var tabs = OS.$('#taskbar-tabs');
    if (!tabs) return;
    tabs.innerHTML = '';
    wm.windows.forEach(function (win) {
      if (win.type === 'preview' && OS.layout.form === 'phone') return;
      var tab = OS.el('button', 'tab');
      var dot = OS.el('span', 'tab-dot');
      dot.hidden = !win.badgeEl || win.badgeEl.hidden;
      tab.appendChild(dot);
      tab.appendChild(OS.el('span', 'tab-title', win.titleEl.textContent));
      tab.addEventListener('click', function () {
        if (win.el.classList.contains('hidden-win')) {
          win.el.classList.remove('hidden-win');
          wm.relayoutPhone();
        } else if (wm._active === win && OS.layout.form !== 'phone') {
          wm.minimize(win);
          return;
        }
        wm.focus(win);
      });
      if (wm._active === win && !win.el.classList.contains('hidden-win')) {
        tab.classList.add('active');
      }
      tabs.appendChild(tab);
    });
  }
  wm.renderTabs = renderTabs;

  function buildStartMenu() {
    var menu = OS.$('#start-menu');
    menu.innerHTML = '';
    [
      {icon: 'edit', label: OS.t('ctx_new_note'), type: 'notepad'},
      {icon: 'folder', label: OS.t('ctx_file_transfer'), type: 'files'},
      {icon: 'bolt', label: OS.t('ctx_quick_actions'), type: 'actions'},
      {icon: 'server', label: OS.t('ctx_host_manager'), type: 'hosts'},
      {icon: 'gear', label: OS.t('ctx_system_settings'), type: 'settings'}
    ].forEach(function (item) {
      var btn = OS.el('button', 'menu-item');
      var mi = OS.el('span', 'mi-icon');
      mi.innerHTML = OS.iconSvg(item.icon);
      btn.appendChild(mi);
      btn.appendChild(OS.el('span', '', item.label));
      btn.addEventListener('click', function () {
        menu.hidden = true;
        wm.openApp(item.type);
      });
      menu.appendChild(btn);
    });
  }

  function initStartMenu() {
    buildStartMenu();
    var btn = OS.$('#start-btn');
    var menu = OS.$('#start-menu');
    btn.addEventListener('click', function (e) {
      e.stopPropagation();
      menu.hidden = !menu.hidden;
      btn.classList.toggle('active', !menu.hidden);
    });
    document.addEventListener('click', function (e) {
      if (!menu.hidden && !menu.contains(e.target) && e.target !== btn) {
        menu.hidden = true;
        btn.classList.remove('active');
      }
    });
  }

  // ================= phone nav / app switcher =================
  function initPhoneNav() {
    $$('#phone-nav button').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var nav = btn.dataset.nav;
        $$('#phone-nav button').forEach(function (b) { b.classList.remove('active'); });
        btn.classList.add('active');
        if (nav === 'hosts') {
          wm.windows.forEach(function (w) { w.el.classList.add('hidden-win'); });
          wm.relayoutPhone();
        } else if (nav === 'terminal') {
          openAppSwitcher();
        } else {
          wm.openApp(nav);
        }
      });
    });
  }

  function openAppSwitcher() {
    var sw = OS.$('#app-switcher');
    sw.innerHTML = '';
    var sessions = [];
    if (OS.term && OS.term.sessions) {
      OS.term.sessions.forEach(function (s) {
        sessions.push({label: s.name || s.hostName || 'session', sub: 'terminal',
          open: function () { s.win.el.classList.remove('hidden-win'); wm.focus(s.win); }});
      });
    }
    wm.windows.forEach(function (w) {
      if (w.type === 'terminal') return;
      sessions.push({label: w.titleEl.textContent, sub: w.type,
        open: function () { w.el.classList.remove('hidden-win'); wm.focus(w); }});
    });
    if (!sessions.length) {
      sw.appendChild(OS.el('div', 'menu-item', OS.t('no_sessions')));
    }
    sessions.forEach(function (s) {
      var btn = OS.el('button', 'menu-item');
      var mi = OS.el('span', 'mi-icon');
      mi.innerHTML = OS.iconSvg('chevron');
      btn.appendChild(mi);
      var col = OS.el('div');
      col.appendChild(OS.el('div', '', s.label));
      col.appendChild(OS.el('div', '', OS.el('span', '', s.sub)));
      btn.appendChild(col);
      btn.addEventListener('click', function () {
        sw.hidden = true;
        s.open();
      });
      sw.appendChild(btn);
    });
    sw.hidden = false;
    setTimeout(function () {
      function closer(e) {
        if (!sw.contains(e.target)) { sw.hidden = true; document.removeEventListener('click', closer); }
      }
      document.addEventListener('click', closer);
    }, 30);
  }

  // ================= desktop icons & phone host list =================
  function hostById(id) {
    return OS.state.hosts.find(function (h) { return h.id === id; });
  }
  OS.hostById = hostById;

  function statusClass(id) {
    var st = OS.state.status[id];
    return st === true ? 'dot-on' : (st === false ? 'dot-off' : 'dot-unknown');
  }

  function renderHosts() {
    var grid = OS.$('#icon-grid');
    var list = OS.$('#phone-host-list');
    grid.innerHTML = '';
    list.innerHTML = '';
    OS.$('#desktop-empty').hidden = OS.state.hosts.length > 0;
    OS.state.hosts.forEach(function (host) {
      // desktop icon
      var icon = OS.el('button', 'desktop-icon');
      var glyph = OS.el('div', 'host-icon-glyph');
      glyph.appendChild(OS.icon('server'));
      var dot = OS.el('span', statusClass(host.id));
      glyph.appendChild(dot);
      icon.appendChild(glyph);
      icon.appendChild(OS.el('span', 'icon-name', host.name));
      if (OS.state.status[host.id] === false) icon.classList.add('offline');
      icon.addEventListener('click', function () { OS.term.open(host.id); });
      icon.addEventListener('contextmenu', function (e) {
        e.preventDefault();
        hostMenu(host, e.clientX, e.clientY);
      });
      grid.appendChild(icon);

      // phone card
      var card = OS.el('button', 'phone-host-card');
      var cg = OS.el('div', 'host-icon-glyph');
      cg.appendChild(OS.icon('server'));
      cg.appendChild(OS.el('span', statusClass(host.id)));
      var meta = OS.el('div', 'phone-host-meta');
      meta.appendChild(OS.el('div', 'phone-host-name', host.name));
      meta.appendChild(OS.el('div', 'phone-host-sub',
        host.username + '@' + host.host + ':' + host.port));
      var att = OS.el('div', 'phone-host-attention');
      att.hidden = true;
      meta.appendChild(att);
      card.appendChild(cg);
      card.appendChild(meta);
      if (OS.state.status[host.id] === false) card.style.opacity = '0.6';
      card.addEventListener('click', function () { OS.term.open(host.id); });
      var pressTimer = null;
      card.addEventListener('touchstart', function (e) {
        pressTimer = setTimeout(function () {
          pressTimer = null;
          var t = e.changedTouches[0];
          hostMenu(host, t.clientX, t.clientY);
        }, 550);
      }, {passive: true});
      card.addEventListener('touchend', function () { clearTimeout(pressTimer); });
      card.addEventListener('touchmove', function () { clearTimeout(pressTimer); });
      card._attentionEl = att;
      list.appendChild(card);
    });
    updateTopbar();
  }

  function hostMenu(host, x, y) {
    OS.ctxMenu([
      {icon: 'terminal', label: OS.t('ctx_open_terminal'),
       onClick: function () { OS.term.open(host.id); }},
      {icon: 'folder', label: OS.t('ctx_browse_files'),
       onClick: function () { OS.files.open({hostId: host.id}); }},
      '-',
      {icon: 'edit', label: OS.t('ctx_edit_host'),
       onClick: function () { openHostManager(host.id); }},
      {icon: 'close', label: OS.t('ctx_delete_host'), danger: true,
       onClick: function () {
         OS.confirm(OS.t('hm_confirm_delete', {name: host.name}), true)
           .then(function (yes) {
             if (!yes) return;
             OS.api('/api/hosts/' + host.id, {method: 'DELETE'})
               .then(refreshHosts);
           });
       }}
    ], x, y);
  }

  function refreshHosts() {
    return OS.api('/api/hosts').then(function (hosts) {
      OS.state.hosts = hosts || [];
      hosts.forEach(function (h) {
        if (h.online !== undefined) OS.state.status[h.id] = h.online;
      });
      renderHosts();
      OS.bus.emit('hosts-changed');
    });
  }
  OS.refreshHosts = refreshHosts;

  // ================= top bar =================
  function updateTopbar() {
    var total = OS.state.hosts.length;
    var online = 0;
    Object.keys(OS.state.status).forEach(function (k) {
      if (OS.state.status[k] === true) online++;
    });
    OS.$('#tb-online').textContent = OS.t('topbar_online', {n: online, t: total});

    var waiting = Object.keys(OS.state.attention).length;
    var wEl = OS.$('#tb-waiting');
    wEl.hidden = waiting === 0;
    wEl.textContent = OS.t('topbar_waiting', {n: waiting});
  }
  OS.updateTopbar = updateTopbar;

  function initClock() {
    function tick() {
      var el = OS.$('#tb-clock');
      if (!el) return;
      var d = new Date();
      var pad = function (n) { return (n < 10 ? '0' : '') + n; };
      el.textContent = pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' +
        pad(d.getSeconds());
    }
    tick();
    setInterval(tick, 1000);
  }

  function initSession() {
    OS.api('/api/session').then(function (info) {
      OS.state.session = info;
      updateSessionChip();
      setInterval(updateSessionChip, 60000);
    });
  }

  function updateSessionChip() {
    var el = OS.$('#tb-session');
    if (!el || !OS.state.session) return;
    var hours = Math.floor(OS.state.session.remaining_seconds / 3600);
    var days = Math.floor(hours / 24);
    el.textContent = days >= 1
      ? OS.t('topbar_session_days', {d: days})
      : OS.t('topbar_session_hours', {d: hours});
  }

  // ================= Host Manager window =================
  function openHostManager(editId) {
    var existing = null;
    wm.windows.forEach(function (w) { if (w.type === 'hosts') existing = w; });
    var win = wm.openApp('hosts', {
      update: function (w) { if (editId !== undefined) hostForm(w, editId); }
    });
    // for a freshly created window the update hook never runs
    if (!existing && win && editId !== undefined) hostForm(win, editId);
  }
  OS.openHostManager = openHostManager;

  function renderHostsTable(win) {
    var tbody = OS.$('.hosts-tbody', win.body);
    tbody.innerHTML = '';
    OS.state.hosts.forEach(function (host) {
      var tr = OS.el('tr');
      var tdName = OS.el('td');
      var dot = OS.el('span', 'host-status-dot ' + statusClass(host.id));
      tdName.appendChild(dot);
      tdName.appendChild(document.createTextNode(host.name));
      tr.appendChild(tdName);
      tr.appendChild(OS.el('td', 'mono', host.host + ':' + host.port));
      tr.appendChild(OS.el('td', '', host.username));
      tr.appendChild(OS.el('td', '', host.online === true ? OS.t('status_online') :
        host.online === false ? OS.t('status_offline') : OS.t('status_unknown')));
      var tdAct = OS.el('td');
      var editBtn = OS.el('button', 'btn btn-sm', OS.t('edit'));
      editBtn.addEventListener('click', function () { hostForm(win, host.id); });
      tdAct.appendChild(editBtn);
      tr.appendChild(tdAct);
      tbody.appendChild(tr);
    });
    if (!OS.state.hosts.length) {
      var tr = OS.el('tr');
      var td = OS.el('td', '', OS.t('no_hosts'));
      td.colSpan = 5;
      tr.appendChild(td);
      tbody.appendChild(tr);
    }
  }

  function hostForm(win, hostId) {
    var host = hostId ? hostById(hostId) : null;
    var wrap = OS.$('.hosts-table-wrap', win.body);
    var existingForm = OS.$('.host-form-pane', win.body);
    if (existingForm) existingForm.remove();

    var pane = OS.el('div', 'host-form-pane');
    var tpl = document.getElementById('tpl-host-form');
    var form = document.importNode(tpl.content, true).firstElementChild;
    pane.appendChild(form);

    var titleEl = OS.el('h3', '', host ? OS.t('edit') + ' — ' + host.name : OS.t('hm_add'));
    pane.insertBefore(titleEl, form);
    if (host) {
      form.name.value = host.name;
      form.group_name.value = host.group_name || '';
      form.host.value = host.host;
      form.port.value = host.port;
      form.username.value = host.username;
      form.auth_method.value = host.auth_method;
      form.key_path.value = host.key_path || '';
      if (host.needs_password) OS.$('.host-needs-pw', pane).hidden = false;
    }
    var pwField = OS.$('.host-f-password', pane);
    var keyField = OS.$('.host-f-key', pane);
    function syncAuth() {
      var isKey = form.auth_method.value === 'key';
      keyField.hidden = !isKey;
      pwField.querySelector('.field-label').textContent = isKey
        ? 'Passphrase' : OS.t('f_password');
    }
    form.auth_method.addEventListener('change', syncAuth);
    syncAuth();

    var saveBtn = OS.el('button', 'btn btn-primary btn-sm', OS.t('save'));
    saveBtn.style.marginTop = '10px';
    var cancelBtn = OS.el('button', 'btn btn-sm', OS.t('cancel'));
    cancelBtn.style.margin = '10px 0 0 8px';
    pane.appendChild(saveBtn);
    pane.appendChild(cancelBtn);
    cancelBtn.addEventListener('click', function () { pane.remove(); });
    saveBtn.addEventListener('click', function () {
      var payload = {
        name: form.name.value, group_name: form.group_name.value,
        host: form.host.value, port: form.port.value,
        username: form.username.value, auth_method: form.auth_method.value,
        key_path: form.key_path.value
      };
      // only send a password when the user typed one — an absent key keeps
      // the stored one, an empty string would clear it
      if (form.password.value) payload.password = form.password.value;
      var req = host
        ? OS.api('/api/hosts/' + host.id, {method: 'PUT', body: payload})
        : OS.api('/api/hosts', {method: 'POST', body: payload});
      req.then(function (res) {
        if (res.error) { OS.toast(res.error, true); return; }
        OS.toast(OS.t('hm_saved'));
        pane.remove();
        refreshHosts().then(function () {
          if (win.el.isConnected) renderHostsTable(win);
        });
      });
    });

    wrap.hidden = true;
    pane.classList.add('hosts-table-wrap');
    wrap.parentNode.insertBefore(pane, wrap.nextSibling);
  }

  // window init hook
  OS.winInit.hosts = function (win) {
    renderHostsTable(win);
    OS.$('.host-add', win.body).addEventListener('click', function () {
      hostForm(win, null);
    });
    var unsub = OS.bus.on('hosts-changed', function () {
      if (win.el.isConnected) renderHostsTable(win);
    });
    var origClose = win.el.addEventListener.bind(win.el);
    OS.winClose.hosts = function () { unsub(); };
  };

  // ================= socket-driven updates =================
  function initSocketHandlers() {
    if (!OS.socket) return;
    OS.socket.on('host_status', function (data) {
      OS.state.status[data.host_id] = data.online;
      renderHosts();
    });
    OS.socket.on('hosts_changed', function () { refreshHosts(); });
    OS.socket.on('session_attention', function (data) {
      OS.state.attention[data.sid] = data;
      updateTopbar();
      var win = wm.findBySid(data.sid);
      if (win) {
        win.setAttention(OS.t('waiting_input', {prompt: data.hint || data.name}),
          data.confidence);
      }
      OS.bus.emit('attention', data);
    });
    OS.socket.on('session_attention_cleared', function (data) {
      delete OS.state.attention[data.sid];
      updateTopbar();
      var win = wm.findBySid(data.sid);
      if (win) win.setAttention('', '');
      OS.bus.emit('attention-cleared', data);
    });
    OS.$('#tb-waiting').addEventListener('click', function () {
      var sid = Object.keys(OS.state.attention)[0];
      if (!sid) return;
      var win = wm.findBySid(sid);
      if (win) { win.el.classList.remove('hidden-win'); wm.focus(win); }
    });
    OS.$('#tb-online').addEventListener('click', function () {
      openHostManager();
    });
    OS.$('#tb-logout').addEventListener('click', function () {
      window.location.href = '/auth/logout';
    });
  }
  OS.initSocketHandlers = initSocketHandlers;

  // ================= boot pieces run from boot.js =================
  OS.desktop = {
    init: function () {
      wm.init();
      initStartMenu();
      initPhoneNav();
      initClock();
      initSession();
      initSocketHandlers();
      refreshHosts();
    }
  };
})();
