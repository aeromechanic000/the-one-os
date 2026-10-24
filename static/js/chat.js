/* chat.js — AI assistant panel: right-side slide-out (top bar toggle),
   conversation persisted in localStorage, assistant replies rendered as
   sanitized Markdown via the vendored marked + DOMPurify (IDEA.md §6.10). */
(function () {
  'use strict';
  var OS = window.OS;

  var MAX_SAVED = 100;  // messages kept in localStorage
  var panel, msgsEl, inputEl;
  var busy = false;
  var messages = OS.store.get('ai-chat', []);  // [{role: 'user'|'assistant', content}]

  function persist() {
    OS.store.set('ai-chat', messages.slice(-MAX_SAVED));
  }

  function render() {
    msgsEl.innerHTML = '';
    if (!messages.length) {
      msgsEl.appendChild(OS.el('div', 'ai-empty', OS.t('ai_hint_history')));
      return;
    }
    messages.forEach(function (m) {
      var row = OS.el('div', 'ai-msg ' +
        (m.role === 'user' ? 'ai-msg-user' : 'ai-msg-bot'));
      var bubble = OS.el('div', 'ai-bubble' +
        (m.role === 'user' ? '' : ' preview-md'));
      if (m.role === 'user') {
        bubble.textContent = m.content;
      } else if (window.marked && window.DOMPurify) {
        bubble.innerHTML = window.DOMPurify.sanitize(
          window.marked.parse(m.content, {breaks: true, mangle: false}));
      } else {
        bubble.textContent = m.content;
      }
      row.appendChild(bubble);
      msgsEl.appendChild(row);
    });
    msgsEl.scrollTop = msgsEl.scrollHeight;
  }

  function send() {
    var text = (inputEl.value || '').trim();
    if (!text || busy) return;
    inputEl.value = '';
    inputEl.style.height = 'auto';
    messages.push({role: 'user', content: text});
    persist();
    busy = true;
    render();
    var thinking = OS.el('div', 'ai-msg ai-msg-bot');
    thinking.appendChild(OS.el('div', 'ai-bubble', OS.t('ai_thinking')));
    msgsEl.appendChild(thinking);
    msgsEl.scrollTop = msgsEl.scrollHeight;
    OS.api('/api/ai/chat', {method: 'POST', body: {messages: messages}})
      .then(function (res) {
        thinking.remove();
        busy = false;
        if (res.error) {
          OS.toast(res.error, true);
          render();
          return;
        }
        messages.push({role: 'assistant', content: res.reply || ''});
        persist();
        render();
      })
      .catch(function () {
        thinking.remove();
        busy = false;
        render();
        OS.toast(OS.t('operation_failed', {msg: 'network'}), true);
      });
  }

  function toggle(force) {
    var open = (force !== undefined)
      ? force : !panel.classList.contains('open');
    panel.classList.toggle('open', open);
    panel.setAttribute('aria-hidden', String(!open));
    if (open) {
      render();
      setTimeout(function () { inputEl.focus(); }, 180);
    }
  }

  OS.chat = {
    init: function () {
      panel = OS.$('#ai-panel');
      if (!panel) return;
      msgsEl = OS.$('#ai-messages');
      inputEl = OS.$('#ai-input');
      OS.$('#ai-clear').appendChild(OS.icon('trash'));
      OS.$('#ai-close').appendChild(OS.icon('close'));
      OS.$('#tb-ai').addEventListener('click', function (e) {
        e.stopPropagation();
        toggle();
      });
      OS.$('#ai-close').addEventListener('click', function () { toggle(false); });
      OS.$('#ai-clear').addEventListener('click', function () {
        messages = [];
        persist();
        render();
      });
      OS.$('#ai-send').addEventListener('click', send);
      inputEl.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          send();
        }
      });
      inputEl.addEventListener('input', function () {
        inputEl.style.height = 'auto';
        inputEl.style.height = Math.min(inputEl.scrollHeight, 110) + 'px';
      });
      document.addEventListener('click', function (e) {
        if (panel.classList.contains('open') &&
            !panel.contains(e.target) && !e.target.closest('#tb-ai')) {
          toggle(false);
        }
      });
      document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && panel.classList.contains('open')) {
          toggle(false);
        }
      });
      render();
    },
    toggle: function (force) { if (panel) toggle(force); }
  };
})();
