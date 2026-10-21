/* layout.js — three screen forms + orientation (IDEA.md §4.1).
   Width >= 1024 desktop; 768-1023 tablet; < 768 phone. The body carries a
   form-* class that CSS and JS branch on; OS.layout.onChange subscribers
   re-fit terminals and windows on every switch. */
(function () {
  'use strict';
  var OS = window.OS;

  function compute() {
    var w = window.innerWidth;
    var form = w >= 1024 ? 'desktop' : (w >= 768 ? 'tablet' : 'phone');
    var orientation = window.innerHeight >= w ? 'portrait' : 'landscape';
    return {form: form, orientation: orientation, width: w};
  }

  var current = null;
  var subs = [];

  function apply() {
    var next = compute();
    var changed = !current || next.form !== current.form ||
      next.orientation !== current.orientation;
    current = next;
    document.body.classList.remove('form-desktop', 'form-tablet', 'form-phone',
      'orientation-portrait', 'orientation-landscape');
    document.body.classList.add('form-' + next.form,
      'orientation-' + next.orientation);

    // structural visibility: taskbar/topbar handled by CSS; phone pieces here
    var phoneNav = OS.$('#phone-nav');
    var phoneHome = OS.$('#phone-home');
    if (phoneNav) phoneNav.hidden = next.form !== 'phone';
    if (phoneHome) {
      phoneHome.hidden = next.form !== 'phone' ||
        (OS.wm && OS.wm.hasVisible());
    }
    if (changed) subs.forEach(function (fn) { fn(next); });
  }

  OS.layout = {
    get form() { return current ? current.form : 'desktop'; },
    get orientation() { return current ? current.orientation : 'landscape'; },
    get isPhone() { return current.form === 'phone'; },
    get isTouch() {
      return 'ontouchstart' in window || navigator.maxTouchPoints > 0;
    },
    onChange: function (fn) { subs.push(fn); },
    init: function () {
      var timer = null;
      function schedule() {
        clearTimeout(timer);
        timer = setTimeout(apply, 120);
      }
      window.addEventListener('resize', schedule);
      window.addEventListener('orientationchange', schedule);
      apply();
    }
  };
})();
