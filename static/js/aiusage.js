/* aiusage.js — AI Service Usage Monitor window (IDEA.md AI assistant
   extension). Provider templates quick-fill an editable per-token config;
   "Check" probes the usage endpoint server-side — the token itself never
   comes back to the client (only ••••last4). */
(function () {
  'use strict';
  var OS = window.OS;

  var PROVIDERS = [];   // template meta from GET /api/ai/tokens

  function providerName(id) {
    for (var i = 0; i < PROVIDERS.length; i++) {
      if (PROVIDERS[i].id === id) return PROVIDERS[i].name;
    }
    return id === 'custom' ? OS.t('aiu_custom') : id;
  }

  function load() {
    return OS.api('/api/ai/tokens').then(function (res) {
      PROVIDERS = res.providers || [];
      return res.tokens || [];
    });
  }

  function refresh(win) {
    load().then(function (tokens) {
      if (win.el.isConnected) renderList(win, tokens);
    });
  }

  // ---------- list ----------------------------------------------------------

  function renderList(win, tokens) {
    var list = OS.$('.aiu-list', win.body);
    list.innerHTML = '';
    if (!tokens.length) {
      var empty = OS.el('div', 'aiu-empty', OS.t('aiu_empty'));
      list.appendChild(empty);
      return;
    }
    tokens.forEach(function (tok) {
      list.appendChild(renderRow(win, tok));
    });
  }

  function renderRow(win, tok) {
    var row = OS.el('div', 'aiu-row');
    row.dataset.id = tok.id;

    var head = OS.el('div', 'aiu-head');
    head.appendChild(OS.el('span', 'aiu-label', tok.label));
    head.appendChild(OS.el('span', 'aiu-prov', providerName(tok.provider)));
    head.appendChild(OS.el('span', 'mono aiu-mask', '••••' + tok.last4));
    head.appendChild(OS.el('span', 'aiu-spacer'));

    var checkBtn = OS.el('button', 'btn btn-sm', OS.t('aiu_check'));
    checkBtn.addEventListener('click', function () {
      checkBtn.disabled = true;
      check(win, tok.id);
    });
    var editBtn = OS.el('button', 'btn btn-sm', OS.t('aiu_edit'));
    editBtn.addEventListener('click', function () { form(win, tok); });
    var delBtn = OS.el('button', 'btn btn-danger btn-sm', OS.t('delete'));
    delBtn.addEventListener('click', function () {
      OS.confirm(OS.t('aiu_confirm_delete', {name: tok.label}), true)
        .then(function (yes) {
          if (!yes) return;
          OS.api('/api/ai/tokens/' + tok.id, {method: 'DELETE'})
            .then(function () { refresh(win); });
        });
    });
    head.appendChild(checkBtn);
    head.appendChild(editBtn);
    head.appendChild(delBtn);
    row.appendChild(head);

    if (tok.last_check) row.appendChild(renderCheck(tok.last_check));

    var when = OS.el('div', 'aiu-when',
      tok.last_check_at
        ? OS.t('aiu_last_check', {when: relTime(tok.last_check_at)})
        : OS.t('aiu_never'));
    row.appendChild(when);
    return row;
  }

  function renderCheck(check) {
    var box = OS.el('div', 'aiu-metrics');
    if (!check.ok) {
      box.appendChild(OS.el('div', 'aiu-error', check.error || '—'));
      return box;
    }
    (check.metrics || []).forEach(function (m) {
      var line = OS.el('div', 'aiu-metric');
      line.appendChild(OS.el('span', 'aiu-metric-label', m.label));
      line.appendChild(OS.el('span', 'mono', m.value));
      if (m.window) {
        line.appendChild(OS.el('span', 'aiu-window', windowLabel(m.window)));
      }
      box.appendChild(line);
    });
    if (check.note) {
      box.appendChild(OS.el('div', 'aiu-note', check.note));
    }
    return box;
  }

  function windowLabel(win) {
    var map = {'5h': OS.t('aiu_window_5h'), '7d': OS.t('aiu_window_7d'),
               '30d': OS.t('aiu_window_30d')};
    return map[win] || win;
  }

  // SQLite datetime('now') is UTC; parse as such for relative display.
  function relTime(sqliteUtc) {
    var ms = Date.parse(String(sqliteUtc).replace(' ', 'T') + 'Z');
    return isNaN(ms) ? sqliteUtc : OS.relTime(ms / 1000);
  }

  function check(win, tid) {
    OS.api('/api/ai/tokens/' + tid + '/check', {method: 'POST'})
      .then(function (res) {
        if (res.error) OS.toast(res.error, true);
        refresh(win);
      });
  }

  function checkAll(win, done) {
    var btn = OS.$('.aiu-refresh', win.body);
    if (btn) btn.disabled = true;
    load().then(function (tokens) {
      var chain = Promise.resolve();
      tokens.forEach(function (tok) {
        chain = chain.then(function () {
          return OS.api('/api/ai/tokens/' + tok.id + '/check',
                        {method: 'POST'});
        }).then(function () { refresh(win); });
      });
      return chain;
    }).catch(function () { /* transient — next cycle retries */ })
      .then(function () {
        if (btn && win.el.isConnected) btn.disabled = false;
        if (done) done();
      });
  }

  // ---------- add / edit form ------------------------------------------------

  // Make quick-filled template values unmistakable: accent tint on the
  // filled fields plus a note saying only the API token is left to paste.
  function markTemplate(el, meta) {
    var on = !!meta;
    [el.label, el.base_url, el.auth_style, el.usage_url, el.extract_path]
      .forEach(function (f) { f.classList.toggle('aiu-template-filled', on); });
    var note = OS.$('.aiu-tpl-note', el);
    if (note) {
      note.hidden = !on;
      if (on) note.textContent = OS.t('aiu_template_applied', {name: meta.name});
    }
    if (on) el.token.focus();
  }

  function form(win, existing) {
    var old = OS.$('.aiu-form-pane', win.body);
    if (old) old.remove();

    var pane = OS.el('div', 'host-form-pane aiu-form-pane');
    var tpl = document.getElementById('tpl-aiu-form');
    var el = document.importNode(tpl.content, true).firstElementChild;
    pane.appendChild(el);
    var titleEl = OS.el('h3', '', existing
      ? OS.t('aiu_edit') + ' — ' + existing.label : OS.t('aiu_add'));
    pane.insertBefore(titleEl, el);

    var tplRow = el.querySelector('.aiu-tpl-row');
    var sel = el.template;
    if (existing) {
      tplRow.hidden = true;   // edit the config directly, no re-templating
    } else {
      var customOpt = OS.el('option', '',
        '— ' + OS.t('aiu_custom_manual') + ' —');
      customOpt.value = 'custom';
      sel.appendChild(customOpt);
      PROVIDERS.forEach(function (p) {
        var opt = OS.el('option', '', p.name);
        opt.value = p.id;
        sel.appendChild(opt);
      });
      sel.value = 'custom';
      sel.addEventListener('change', function () {
        var meta = null;
        PROVIDERS.forEach(function (p) { if (p.id === sel.value) meta = p; });
        el.label.value = meta ? meta.name : '';
        el.base_url.value = meta ? meta.base_url : '';
        el.auth_style.value = meta ? meta.auth_style : 'bearer';
        el.usage_url.value = meta ? meta.usage_url : '';
        el.extract_path.value = meta ? meta.extract_path : '';
        markTemplate(el, meta);
      });
    }

    if (existing) {
      el.label.value = existing.label;
      el.base_url.value = existing.base_url;
      el.auth_style.value = existing.auth_style;
      el.usage_url.value = existing.usage_url;
      el.extract_path.value = existing.extract_path;
      el.token.placeholder = OS.t('aiu_token_keep');
      OS.$('.aiu-token-hint', pane).textContent = OS.t('aiu_token_keep');
    }

    var saveBtn = OS.el('button', 'btn btn-primary btn-sm', OS.t('aiu_save'));
    saveBtn.style.marginTop = '10px';
    var cancelBtn = OS.el('button', 'btn btn-sm', OS.t('cancel'));
    cancelBtn.style.margin = '10px 0 0 8px';
    pane.appendChild(saveBtn);
    pane.appendChild(cancelBtn);
    cancelBtn.addEventListener('click', function () { pane.remove(); });

    saveBtn.addEventListener('click', function () {
      var payload = {
        label: el.label.value, base_url: el.base_url.value,
        auth_style: el.auth_style.value, usage_url: el.usage_url.value,
        extract_path: el.extract_path.value
      };
      if (el.token.value) payload.token = el.token.value;
      var req = existing
        ? OS.api('/api/ai/tokens/' + existing.id,
                 {method: 'PUT', body: payload})
        : OS.api('/api/ai/tokens', {method: 'POST', body: Object.assign(
            {provider: sel.value}, payload)});
      req.then(function (res) {
        if (res.error) { OS.toast(res.error, true); return; }
        pane.remove();
        refresh(win);
      });
    });

    var listEl = OS.$('.aiu-list', win.body);
    listEl.parentNode.insertBefore(pane, listEl);
  }

  // ---------- window hook ------------------------------------------------------

  var autoTimer = null;

  function stopAuto() {
    if (autoTimer) { clearInterval(autoTimer); autoTimer = null; }
  }

  // Auto-refresh cadence from the aiu_autorefresh setting (minutes, 0 = off);
  // runs only while the window is open and the tab is visible.
  function startAuto(win) {
    stopAuto();
    OS.api('/api/settings').then(function (s) {
      var mins = parseInt(s.aiu_autorefresh, 10);
      if (!mins || mins < 1 || !win.el.isConnected) return;
      var busy = false;
      autoTimer = setInterval(function () {
        if (!win.el.isConnected) { stopAuto(); return; }
        if (document.hidden || busy) return;
        busy = true;
        checkAll(win, function () { busy = false; });
      }, mins * 60000);
    });
  }

  OS.winInit.aiusage = function (win) {
    refresh(win);
    OS.$('.aiu-add', win.body).addEventListener('click', function () {
      form(win, null);
    });
    OS.$('.aiu-refresh', win.body).addEventListener('click', function () {
      checkAll(win);
    });
    startAuto(win);
  };
  OS.winClose.aiusage = stopAuto;
})();
