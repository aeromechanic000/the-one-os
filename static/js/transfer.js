/* transfer.js — transfer queue view + live progress (IDEA.md §6.7). */
(function () {
  'use strict';
  var OS = window.OS;

  var transfer = OS.transfer = {
    boundEl: null,

    init: function () {
      OS.bus.on('transfer_progress', function (data) {
        transfer.refreshRow(transfer.boundEl, data);
      });
    },

    bind: function (el) {
      transfer.boundEl = el;
      transfer.refresh(el);
    },

    refresh: function (el) {
      if (!el) return;
      OS.api('/api/transfer/queue').then(function (tasks) {
        el.innerHTML = '';
        (tasks || []).forEach(function (task) {
          transfer.addRow(el, task);
        });
      });
    },

    addRow: function (el, task) {
      var row = OS.el('div', 'transfer-row');
      row.dataset.id = task.id;
      row.appendChild(OS.el('span', 'file-name', task.name));
      var bar = OS.el('div', 'transfer-bar');
      var fill = OS.el('div');
      fill.style.width = (task.percent || 0) + '%';
      bar.appendChild(fill);
      row.appendChild(bar);
      row.appendChild(transfer.stateEl(task));
      el.insertBefore(row, el.firstChild);
      return row;
    },

    refreshRow: function (el, data) {
      if (!el) return;
      var row = el.querySelector('[data-id="' + data.id + '"]');
      if (!row) {
        row = transfer.addRow(el, data);
      } else {
        var fill = row.querySelector('.transfer-bar > div');
        if (fill) fill.style.width = (data.percent || 0) + '%';
        var st = row.querySelector('.transfer-state');
        if (st) st.replaceWith(transfer.stateEl(data));
      }
    },

    stateEl: function (task) {
      var label;
      if (task.state === 'queued') label = OS.t('q_queued');
      else if (task.state === 'transferring')
        label = OS.t('q_transferring', {p: task.percent || 0});
      else if (task.state === 'completed') label = OS.t('q_completed');
      else if (task.state === 'running')
        label = OS.t('q_transferring', {p: task.percent || 0});
      else label = task.error
        ? OS.t('q_failed') + ': ' + task.error.slice(0, 40)
        : OS.t('q_failed');
      var el = OS.el('span', 'transfer-state ' + task.state, label);
      el.title = task.error || '';
      return el;
    }
  };
})();
