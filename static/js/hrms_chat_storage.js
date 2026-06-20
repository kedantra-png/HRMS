/**
 * HRMS chatbot: session-only browser storage (sessionStorage).
 * Cleared on login page and logout so old chats do not reappear after re-login.
 */
(function () {
  'use strict';

  function clearHrmsChatBrowserStorage() {
    try {
      for (let i = sessionStorage.length - 1; i >= 0; i--) {
        const k = sessionStorage.key(i);
        if (k && k.startsWith('hrms-chat-')) sessionStorage.removeItem(k);
      }
      for (let i = localStorage.length - 1; i >= 0; i--) {
        const k = localStorage.key(i);
        if (k && k.startsWith('hrms-chat-')) localStorage.removeItem(k);
      }
    } catch (e) { /* ignore */ }
  }

  window.clearHrmsChatBrowserStorage = clearHrmsChatBrowserStorage;

  document.addEventListener('click', function (e) {
    const link = e.target.closest('a[href]');
    if (!link) return;
    const href = link.getAttribute('href') || '';
    if (href.indexOf('logout') !== -1) clearHrmsChatBrowserStorage();
  });
})();
