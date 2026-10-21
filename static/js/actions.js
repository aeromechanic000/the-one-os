/* actions.js — parameterized quick actions (IDEA.md §6.6): category list,
   dynamic form from the argument spec, client-side validation mirroring the
   backend whitelist, Run + output. */
(function () {
  'use strict';
  var OS = window.OS;
  var $ = OS.$;

  var currentAction = null;

  OS.winInit.actions = function (win) {
    var catsEl = $('.actions-cats', win.body);
    var hostSel = $('.actions-host', win.body);
    var formEl = $('.actions-form', win.body);
    var runBtn = $('.actions-run', win.body);
    var outEl = $('.actions-output', win.body);
    var actions = [];

    function renderHosts() {
      hostSel.innerHTML = '';
      OS.state.hosts.forEach(function (h) {
        var o = OS.el('option', '', h.name);
        o.value = h.id;
        hostSel.appendChild(o);
      });
    }

    function renderCats() {
      catsEl.innerHTML = '';
      var cats = [];
      actions.forEach(function (a) {
        if (cats.indexOf(a.category) < 0) cats.push(a.category);
      });
      cats.forEach(function (cat) {
        var b = OS.el('button', 'cat-btn', cat);
        b.addEventListener('click', function () {
          catsEl.querySelectorAll('.cat-btn').forEach(function (x) {
            x.classList.remove('active');
          });
          b.classList.add('active');
          renderActions(cat);
        });
        catsEl.appendChild(b);
      });
      if (cats.length) {
        catsEl.firstChild.classList.add('active');
        renderActions(cats[0]);
      }
    }

    function renderActions(cat) {
      var bar = OS.el('div');
      bar.style.cssText = 'display:flex;flex-direction:column;gap:4px';
      actions.filter(function (a) { return a.category === cat; })
        .forEach(function (a) {
          var b = OS.el('button', 'btn btn-sm', a.name);
          b.style.textAlign = 'left';
          b.addEventListener('click', function () { selectAction(a); });
          bar.appendChild(b);
        });
      formEl.innerHTML = '';
      formEl.appendChild(bar);
      currentAction = null;
      runBtn.disabled = true;
      outEl.textContent = OS.t('qa_no_selection');
      outEl.classList.add('actions-empty');
    }

    function selectAction(a) {
      currentAction = a;
      runBtn.disabled = false;
      formEl.innerHTML = '';
      var title = OS.el('div', 'actions-title', a.name);
      formEl.appendChild(title);
      if (a.description) {
        formEl.appendChild(OS.el('div', 'actions-desc', a.description));
      }
      (a.arguments || []).forEach(function (arg) {
        var label = OS.el('label', 'field-label',
          arg.label || arg.name);
        formEl.appendChild(label);
        var input;
        if (arg.type === 'select') {
          input = OS.el('select');
          (arg.options || []).forEach(function (opt) {
            var o = OS.el('option', '', String(opt));
            o.value = String(opt);
            input.appendChild(o);
          });
        } else if (arg.type === 'number') {
          input = OS.el('input');
          input.type = 'number';
          if (arg.min !== undefined) input.min = arg.min;
          if (arg.max !== undefined) input.max = arg.max;
        } else {
          input = OS.el('input');
          input.type = 'text';
          input.pattern = arg.pattern || null;
        }
        input.dataset.argName = arg.name;
        input.dataset.argPattern = arg.pattern || '';
        input.value = arg.default !== undefined ? String(arg.default) : '';
        formEl.appendChild(input);
      });
      if (!(a.arguments || []).length) {
        formEl.appendChild(OS.el('div', 'actions-desc', '—'));
      }
    }

    function validate() {
      var params = {};
      var bad = null;
      OS.$$('input, select', formEl).forEach(function (input) {
        if (!input.dataset.argName) return;
        var value = input.value;
        var pattern = input.dataset.argPattern;
        if (pattern) {
          var re = new RegExp('^(?:' + pattern + ')$');
          if (!re.test(value)) bad = bad || input;
        }
        params[input.dataset.argName] = value;
      });
      if (bad) {
        bad.style.borderColor = 'var(--bad)';
        setTimeout(function () { bad.style.borderColor = ''; }, 1500);
        return null;
      }
      return params;
    }

    runBtn.addEventListener('click', function () {
      if (!currentAction) return;
      var params = validate();
      if (!params) {
        outEl.classList.remove('actions-empty');
        outEl.textContent = OS.t('qa_invalid');
        return;
      }
      var hostId = parseInt(hostSel.value, 10);
      if (!hostId) {
        outEl.textContent = OS.t('no_hosts');
        return;
      }
      outEl.classList.remove('actions-empty');
      outEl.textContent = OS.t('qa_running');
      runBtn.disabled = true;
      OS.api('/api/actions/' + currentAction.id + '/run',
        {method: 'POST', body: {host_id: hostId, params: params}})
        .then(function (res) {
          runBtn.disabled = false;
          if (res.error) {
            outEl.textContent = res.error +
              (res.field ? ' (' + res.field + ')' : '');
            return;
          }
          outEl.textContent = '$ ' + (res.command || '') + '\n' +
            (res.output || '') + '\n' +
            OS.t('qa_exit', {code: res.exit_status});
        });
    });

    var unsub = OS.bus.on('hosts-changed', renderHosts);
    OS.winClose.actions = function () { unsub(); };

    renderHosts();
    OS.api('/api/actions').then(function (data) {
      actions = data || [];
      renderCats();
    });
  };
})();
