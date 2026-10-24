/* settings.js — System Settings window (IDEA.md §3.4, §7.7):
   Input & Keyboard (mode, strategy, custom keys, font size),
   Waiting Input Rules (CRUD, live regex validation, sample test,
   import/export), Office Preview method. */
(function () {
  'use strict';
  var OS = window.OS;
  var $ = OS.$;

  var FEATURE_TYPES = [
    ['regex', 'regex'], ['contains_any', 'contains_any'],
    ['line_end_no_newline', 'line_end_no_newline'],
    ['cursor_not_at_col0', 'cursor_not_at_col0'],
    ['command_context', 'command_context'], ['after_interrupt', 'after_interrupt']
  ];

  OS.winInit.settings = function (win) {
    var tabsEl = $('.settings-tabs', win.body);

    tabsEl.addEventListener('click', function (e) {
      var btn = e.target.closest('button');
      if (!btn) return;
      tabsEl.querySelectorAll('button').forEach(function (b) {
        b.classList.remove('active');
      });
      btn.classList.add('active');
      OS.$$('[data-pane]', win.body).forEach(function (pane) {
        pane.hidden = pane.dataset.pane !== btn.dataset.tab;
      });
    });

    initThemePane(win);
    initInputPane(win);
    initRulesPane(win);
    initOfficePane(win);
    initAiPane(win);
    initLimitsPane(win);
  };

  // ================= Appearance (themes) =================
  function initThemePane(win) {
    var grid = OS.$('#theme-grid', win.body);
    if (!grid) return;

    function render() {
      grid.innerHTML = '';
      var current = OS.theme.get();
      OS.theme.THEMES.forEach(function (th) {
        var card = OS.el('button',
          'theme-card' + (th.id === current ? ' active' : ''));
        card.type = 'button';
        var swatch = OS.el('span', 'theme-swatch');
        (OS.theme.SWATCH[th.id] || []).forEach(function (color) {
          var dot = OS.el('span', 'theme-dot');
          dot.style.background = color;
          swatch.appendChild(dot);
        });
        card.appendChild(swatch);
        card.appendChild(OS.el('span', 'theme-name', OS.t(th.nameKey)));
        card.addEventListener('click', function () {
          OS.theme.apply(th.id);
          render();
        });
        grid.appendChild(card);
      });
    }
    render();
  }

  // ================= AI Assistant =================
  function initAiPane(win) {
    var pane = OS.$('[data-pane="ai"]', win.body);
    var baseUrl = OS.$('.ai-base-url', pane);
    var model = OS.$('.ai-model', pane);
    var apiKey = OS.$('.ai-api-key', pane);
    var keyState = OS.$('.ai-key-state', pane);

    OS.api('/api/settings').then(function (s) {
      baseUrl.value = s.ai_base_url || '';
      model.value = s.ai_model || '';
      if (s.ai_api_key_needs_reentry) {
        keyState.textContent = OS.t('ai_api_key_reenter');
        keyState.classList.add('attention-text');
      } else if (s.ai_api_key_set) {
        keyState.textContent = OS.t('ai_api_key_stored');
      }
    });
    OS.$('.ai-save', pane).addEventListener('click', function () {
      var body = {
        ai_base_url: baseUrl.value.trim(),
        ai_model: model.value.trim()
      };
      if (apiKey.value.trim()) body.ai_api_key = apiKey.value.trim();
      OS.api('/api/settings', {method: 'PUT', body: body}).then(function (res) {
        if (res.error) { OS.toast(res.error, true); return; }
        apiKey.value = '';
        if (res.ai_api_key_needs_reentry) {
          keyState.textContent = OS.t('ai_api_key_reenter');
        } else if (res.ai_api_key_set) {
          keyState.textContent = OS.t('ai_api_key_stored');
        } else {
          keyState.textContent = OS.t('ai_api_key_optional');
        }
        keyState.classList.toggle('attention-text', !!res.ai_api_key_needs_reentry);
        OS.toast(OS.t('ss_saved'));
      });
    });
  }

  // ================= Limits & History =================
  function initLimitsPane(win) {
    var pane = OS.$('[data-pane="limits"]', win.body);
    var single = OS.$('.lim-single', pane);
    var storage = OS.$('.lim-storage', pane);
    var history = OS.$('.lim-history', pane);
    var used = OS.$('.lim-used', pane);

    function load() {
      OS.api('/api/settings').then(function (s) {
        single.value = s.max_single_file_mb;
        storage.value = s.max_storage_mb;
        history.value = s.history_days;
      });
      OS.api('/api/files/local').then(function (listing) {
        var total = 0;
        (listing.entries || []).forEach(function (e) {
          if (e.type === 'file') total += e.size || 0;
        });
        used.textContent = OS.t('lim_saved_quota', {
          used: OS.fmtSize(total), total: storage.value + ' MB'});
      });
    }
    load();
    OS.$('.lim-save', pane).addEventListener('click', function () {
      OS.api('/api/settings', {method: 'PUT', body: {
        max_single_file_mb: parseInt(single.value, 10) || 512,
        max_storage_mb: parseInt(storage.value, 10) || 2048,
        history_days: parseInt(history.value, 10) || 90
      }}).then(function (res) {
        if (res.error) { OS.toast(res.error, true); return; }
        OS.toast(OS.t('ss_saved'));
        load();
      });
    });
  }

  // ================= Input & Keyboard =================
  function initInputPane(win) {
    var pane = OS.$('[data-pane="input"]', win.body);

    function pillRow(selector, values, current, onPick) {
      var row = OS.$(selector, pane);
      row.innerHTML = '';
      values.forEach(function (v) {
        var pill = OS.el('button', 'radio-pill' + (v[0] === current ? ' active' : ''), v[1]);
        pill.type = 'button';
        pill.addEventListener('click', function () {
          row.querySelectorAll('.radio-pill').forEach(function (p) {
            p.classList.remove('active');
          });
          pill.classList.add('active');
          var patch = {};
          patch[selector.indexOf('kb-mode') >= 0 ? 'keyboard_mode'
            : selector.indexOf('kb-strategy') >= 0 ? 'keyboard_strategy'
            : 'office_preview_method'] = v[0];
          onPick(patch);
        });
        row.appendChild(pill);
      });
    }

    function render() {
      var s = OS.kb.settings;
      pillRow('.kb-mode-row', [['windows', OS.t('kb_windows')],
        ['mac', OS.t('kb_mac')]], s.mode, save);
      pillRow('.kb-strategy-row', [['coexist', OS.t('kb_coexist')],
        ['replace', OS.t('kb_replace')]], s.strategy, save);

      var slider = OS.$('.kb-fontsize', pane);
      slider.value = OS.store.get('terminal-font-size', 14);
      OS.$('.kb-fontsize-val', pane).textContent = slider.value + 'px';
      renderCustomKeys(pane);
    }

    function save(patch) {
      OS.kb.saveSettings(patch).then(function () {
        OS.toast(OS.t('ss_saved'));
        render();
      });
    }

    // font size applies locally (existing terminals pick it up on rebuild)
    var slider = OS.$('.kb-fontsize', pane);
    slider.addEventListener('input', function () {
      OS.$('.kb-fontsize-val', pane).textContent = slider.value + 'px';
    });
    slider.addEventListener('change', function () {
      OS.api('/api/settings', {method: 'PUT',
        body: {terminal_font_size: slider.value}}).then(function () {
        OS.store.set('terminal-font-size', parseInt(slider.value, 10));
        OS.toast(OS.t('ss_saved'));
      });
    });

    function renderCustomKeys(pane) {
      var listEl = OS.$('.custom-keys-list', pane);
      listEl.innerHTML = '';
      OS.kb.settings.customKeys.forEach(function (ck, idx) {
        var row = OS.el('div', 'ck-row');
        var label = OS.el('input', 'ck-label');
        label.value = ck.label;
        var seq = OS.el('input');
        seq.value = ck.sequence;
        seq.placeholder = OS.t('kb_sequence_hint');
        var del = OS.el('button', 'btn btn-sm btn-danger');
        del.appendChild(OS.icon('close'));
        del.addEventListener('click', function () {
          OS.kb.settings.customKeys.splice(idx, 1);
          save({custom_keys: OS.kb.settings.customKeys});
        });
        row.appendChild(label);
        row.appendChild(seq);
        row.appendChild(del);
        listEl.appendChild(row);
      });
    }

    OS.$('.ck-add', pane).addEventListener('click', function () {
      var label = OS.el('input');
      label.placeholder = OS.t('kb_label');
      var seq = OS.el('input');
      seq.placeholder = OS.t('kb_sequence_hint');
      var body = OS.el('div');
      body.appendChild(label);
      body.appendChild(seq);
      OS.modal({
        title: OS.t('kb_add_key'),
        body: body,
        buttons: [
          {label: OS.t('cancel')},
          {label: OS.t('add'), cls: 'btn-primary', onClick: function () {
            if (!label.value || !seq.value) return false;
            OS.kb.settings.customKeys.push({label: label.value, sequence: seq.value});
            save({custom_keys: OS.kb.settings.customKeys});
          }}
        ]
      });
    });

    render();
  }

  // ================= Waiting Input Rules =================
  function initRulesPane(win) {
    var pane = OS.$('[data-pane="rules"]', win.body);
    var tbody = OS.$('.rules-tbody', pane);

    function load() {
      OS.api('/api/attention/rules').then(function (rules) {
        tbody.innerHTML = '';
        (rules || []).forEach(function (r) {
          var tr = OS.el('tr');
          var tdEn = OS.el('td');
          var cb = OS.el('input');
          cb.type = 'checkbox';
          cb.checked = !!r.enabled;
          cb.addEventListener('change', function () {
            OS.api('/api/attention/rules/' + r.id,
              {method: 'PUT', body: {enabled: cb.checked ? 1 : 0}})
              .then(load);
          });
          tdEn.appendChild(cb);
          tr.appendChild(tdEn);
          var nameCell = OS.el('td', '', r.name);
          if (r.builtin) {
            var chip = OS.el('span', 'ext-chip', OS.t('ar_builtin'));
            chip.style.marginLeft = '6px';
            nameCell.appendChild(chip);
          }
          tr.appendChild(nameCell);
          tr.appendChild(OS.el('td', '', r.confidence));
          tr.appendChild(OS.el('td', '', String(r.priority)));
          tr.appendChild(OS.el('td', '', String(r.hits)));
          var tdAct = OS.el('td');
          var editBtn = OS.el('button', 'btn btn-sm', OS.t('edit'));
          editBtn.addEventListener('click', function () { ruleEditor(r); });
          tdAct.appendChild(editBtn);
          if (!r.builtin) {
            var delBtn = OS.el('button', 'btn btn-sm btn-danger', OS.t('delete'));
            delBtn.style.marginLeft = '6px';
            delBtn.addEventListener('click', function () {
              OS.confirm(OS.t('ar_confirm_delete') + ' (' + r.name + ')', true)
                .then(function (yes) {
                  if (!yes) return;
                  OS.api('/api/attention/rules/' + r.id, {method: 'DELETE'})
                    .then(function (res) {
                      if (res.error) OS.toast(res.error, true);
                      load();
                    });
                });
            });
            tdAct.appendChild(delBtn);
          }
          tr.appendChild(tdAct);
          tbody.appendChild(tr);
        });
      });
    }

    function ruleEditor(rule) {
      var isNew = !rule;
      rule = rule || {name: '', enabled: 1, match_mode: 'any', patterns: [],
        silence_ms: 400, confidence: 'high', priority: 100, window_ms: 30000,
        sample: ''};
      var patterns = rule.patterns || [];

      var paneBody = OS.el('div');
      var tpl = document.getElementById('tpl-rule-editor');
      var editor = document.importNode(tpl.content, true)
        .firstElementChild;
      paneBody.appendChild(editor);
      OS.$('.re-name', editor).value = rule.name;
      OS.$('.re-mode', editor).value = rule.match_mode;
      OS.$('.re-silence', editor).value = rule.silence_ms;
      OS.$('.re-confidence', editor).value = rule.confidence;
      OS.$('.re-priority', editor).value = rule.priority;
      OS.$('.re-window', editor).value = rule.window_ms;
      OS.$('.re-sample', editor).value = rule.sample || '';

      var featsEl = OS.$('.re-features', editor);
      function renderFeatures() {
        featsEl.innerHTML = '';
        patterns.forEach(function (p, idx) {
          var row = OS.el('div', 'feature-row');
          var sel = OS.el('select');
          FEATURE_TYPES.forEach(function (ft) {
            var o = OS.el('option', '', ft[1]);
            o.value = ft[0];
            sel.appendChild(o);
          });
          sel.value = p.type;
          sel.addEventListener('change', function () {
            p = {type: sel.value};
            patterns[idx] = p;
            renderFeatures();
          });
          row.appendChild(sel);
          if (p.type === 'regex' || p.type === 'command_context') {
            var inp = OS.el('input');
            inp.value = p.value || '';
            inp.placeholder = p.type === 'regex' ? 'regex' : 'command regex';
            inp.addEventListener('input', function () {
              p.value = inp.value;
              try {
                new RegExp(inp.value);
                inp.style.borderColor = '';
              } catch (e) {
                inp.style.borderColor = 'var(--bad)';
              }
            });
            row.appendChild(inp);
          } else if (p.type === 'contains_any') {
            var inp2 = OS.el('input');
            inp2.value = (p.values || []).join(', ');
            inp2.placeholder = 'keyword1, keyword2';
            inp2.addEventListener('input', function () { p.values = inp2.value.split(',').map(function (s) { return s.trim(); }).filter(Boolean); });
            row.appendChild(inp2);
          }
          var del = OS.el('button', 'btn btn-sm');
          del.appendChild(OS.icon('close'));
          del.addEventListener('click', function () {
            patterns.splice(idx, 1);
            renderFeatures();
          });
          row.appendChild(del);
          featsEl.appendChild(row);
        });
      }
      renderFeatures();
      OS.$('.re-add-feature', editor).addEventListener('click', function () {
        patterns.push({type: 'regex', value: ''});
        renderFeatures();
      });

      function collect() {
        return {
          name: OS.$('.re-name', editor).value,
          enabled: 1,
          match_mode: OS.$('.re-mode', editor).value,
          silence_ms: parseInt(OS.$('.re-silence', editor).value, 10) || 0,
          confidence: OS.$('.re-confidence', editor).value,
          priority: parseInt(OS.$('.re-priority', editor).value, 10) || 0,
          window_ms: parseInt(OS.$('.re-window', editor).value, 10) || 0,
          sample: OS.$('.re-sample', editor).value,
          patterns: patterns
        };
      }

      OS.$('.re-test', editor).addEventListener('click', function () {
        var result = OS.$('.test-result', editor);
        result.textContent = '...';
        result.className = 'test-result';
        OS.api('/api/attention/rules/test',
          {method: 'POST', body: {rule: collect(),
            sample: OS.$('.re-sample', editor).value}})
          .then(function (res) {
            if (res.error) {
              result.textContent = res.error;
              return;
            }
            if (res.matched) {
              result.className = 'test-result hit';
              result.textContent = OS.t('ar_test_hit') + ' — ' +
                OS.t('ar_hit_features', {features: res.hits.join(', ')}) +
                (res.hint ? ' (' + res.hint + ')' : '');
            } else {
              result.className = 'test-result miss';
              result.textContent = OS.t('ar_test_miss');
            }
          });
      });

      var modal = OS.modal({
        title: isNew ? OS.t('ar_add') : OS.t('edit') + ' — ' + rule.name,
        body: paneBody,
        buttons: [
          {label: OS.t('cancel')},
          {label: OS.t('save'), cls: 'btn-primary', onClick: function () {
            var data = collect();
            var req = isNew
              ? OS.api('/api/attention/rules', {method: 'POST', body: data})
              : OS.api('/api/attention/rules/' + rule.id,
                {method: 'PUT', body: data});
            req.then(function (res) {
              if (res.error) { OS.toast(res.error, true); return false; }
              load();
            });
          }}
        ]
      });
    }

    OS.$('.rule-add', pane).addEventListener('click', function () {
      ruleEditor(null);
    });
    OS.$('.rule-export', pane).addEventListener('click', function () {
      OS.api('/api/attention/rules/export').then(function (payload) {
        var blob = new Blob([JSON.stringify(payload, null, 2)],
          {type: 'application/json'});
        var a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = 'webos-attention-rules.json';
        a.click();
        URL.revokeObjectURL(a.href);
      });
    });
    var importInput = OS.$('.rule-import-file', pane);
    OS.$('.rule-import', pane).addEventListener('click', function () {
      importInput.click();
    });
    importInput.addEventListener('change', function () {
      var f = importInput.files[0];
      if (!f) return;
      f.text().then(function (text) {
        var payload;
        try { payload = JSON.parse(text); }
        catch (e) { OS.toast('invalid JSON', true); return; }
        OS.api('/api/attention/rules/import', {method: 'POST', body: payload})
          .then(function (res) {
            if (res.error) OS.toast(res.error, true);
            else {
              OS.toast(res.imported + ' +, ' + res.skipped + ' skipped');
              load();
            }
          });
      });
      importInput.value = '';
    });

    load();
  }

  // ================= Office Preview =================
  function initOfficePane(win) {
    var pane = OS.$('[data-pane="office"]', win.body);
    var row = OS.$('.office-method-row', pane);
    var status = OS.$('.office-status', pane);
    var officeAvailable = OS.officeAvailable;

    function render() {
      row.innerHTML = '';
      var current = null;
      OS.api('/api/settings').then(function (s) {
        current = s.office_preview_method || 'libreoffice';
        [['libreoffice', OS.t('op_libreoffice')],
         ['frontend', OS.t('op_frontend')],
         ['disabled', OS.t('op_disabled')]].forEach(function (v) {
          var pill = OS.el('button',
            'radio-pill' + (v[0] === current ? ' active' : ''), v[1]);
          pill.type = 'button';
          pill.addEventListener('click', function () {
            OS.api('/api/settings', {method: 'PUT',
              body: {office_preview_method: v[0]}}).then(render);
          });
          row.appendChild(pill);
        });
      });
    }
    render();
    if (status) {
      status.textContent = officeAvailable
        ? 'LibreOffice: detected (' + 'soffice' + ')'
        : 'LibreOffice: not installed — Office preview falls back to ' +
          OS.t('op_frontend').toLowerCase() + ' / ' + OS.t('op_disabled').toLowerCase();
    }
  }
})();
