/* boot.js — startup sequence: layout, socket, settings, hosts, terminals,
   transfer queue wiring. */
(function () {
  'use strict';
  var OS = window.OS;

  function boot() {
    OS.officeAvailable = !!(window.OS_BOOT && window.OS_BOOT.officeAvailable);
    OS.layout.init();
    OS.wm.init();

    // socket.io (same origin; session cookie rides along)
    OS.socket = io({
      transports: ['websocket', 'polling'],
      reconnection: true,
      reconnectionDelay: 800
    });
    OS.socket.on('disconnect', function () {
      OS.toast('Connection lost, reconnecting…', true);
    });

    OS.term.init();
    OS.transfer.init();
    OS.kb.initHardwareDetection();
    OS.desktop.init();   // includes initSocketHandlers + refreshHosts
    OS.chat.init();
    OS.activation.init();   // sleep overlay + wake-up (single active frontend)

    // settings first (keyboard mode etc.), then re-attach live terminals
    OS.kb.loadSettings().then(function () {
      OS.api('/api/terminals').then(function (list) {
        (list || []).forEach(function (meta) {
          if (meta.state !== 'closed') OS.term.attachExisting(meta);
        });
      });
    });

    OS.bus.on('attention', function (data) {
      var hostCard = document.querySelector('#phone-host-list');
      if (!hostCard) return;
      // phone host cards show a waiting hint (taskbar absent on phone)
      OS.state.hosts.forEach(function () { /* cards re-rendered on status */ });
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
