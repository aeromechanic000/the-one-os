/* activation.js — single-active-frontend enforcement (IDEA.md §1: one
   user, many devices). The latest login or wake is the only active
   frontend; every other one falls asleep under a blocking overlay until
   it wakes and re-syncs. Terminal/status mirrors keep flowing while
   asleep, so waking is cheap. */
(function () {
  'use strict';
  var OS = window.OS;

  var act = OS.activation = {
    sleeping: false,
    state: null,          // last /api/activation payload

    init: function () {
      OS.socket.on('activation_state', act.apply);   // on connect/reconnect
      OS.socket.on('activation_changed', act.check); // someone took over
      // safety nets: socket may have died while the tab was hidden
      window.addEventListener('focus', act.check);
      setInterval(act.check, 60000);
      OS.$('#sleep-wake').addEventListener('click', act.wake);
      // the overlay blocks pointers; swallow stray keys aimed below it
      document.addEventListener('keydown', act.swallow, true);
      OS.$('#sleep-layer').addEventListener('contextmenu', function (e) {
        e.preventDefault();
      });
      act.check();
    },

    check: function () {
      OS.api('/api/activation').then(act.apply);
    },

    apply: function (state) {
      if (!state || !state.client_id) return;
      act.state = state;
      var ipEl = OS.$('#tb-ip');
      if (state.your_ip) {
        ipEl.textContent = state.your_ip;
        ipEl.hidden = false;
      }
      act.setSleeping(!state.active, state);
    },

    setSleeping: function (sleep, state) {
      var wasSleeping = act.sleeping;
      act.sleeping = sleep;
      OS.$('#sleep-layer').hidden = !sleep;
      if (sleep) {
        OS.$('#sleep-active').textContent =
          OS.t('sleep_active', {address: state.active_address || '—'});
        OS.$('#sleep-you').textContent =
          OS.t('sleep_you', {address: state.your_ip || '—'});
      } else if (wasSleeping) {
        // became active again: sync to the latest backend state
        if (OS.refreshHosts) OS.refreshHosts();
        OS.bus.emit('activation-woke');
      }
    },

    wake: function () {
      var btn = OS.$('#sleep-wake');
      btn.disabled = true;
      btn.textContent = OS.t('sleep_waking');
      OS.api('/api/activation/wake', {method: 'POST'}).then(function (res) {
        btn.disabled = false;
        btn.textContent = OS.t('sleep_wake');
        if (res.error) { OS.toast(res.error, true); return; }
        act.apply(res);
        OS.toast(OS.t('sleep_woken'));
      });
    },

    swallow: function (e) {
      if (!act.sleeping) return;
      var t = e.target;
      if (t && t.closest && t.closest('#sleep-layer')) return;
      e.preventDefault();
      e.stopPropagation();
    }
  };
})();
