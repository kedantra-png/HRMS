(function () {
  function csrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute('content') || '' : '';
  }

  function ensureFormToken(form) {
    if (!form || form.querySelector('input[name="csrf_token"]')) return;
    var method = (form.getAttribute('method') || 'get').toUpperCase();
    if (method !== 'POST') return;
    var input = document.createElement('input');
    input.type = 'hidden';
    input.name = 'csrf_token';
    input.value = csrfToken();
    form.prepend(input);
  }

  function injectAllFormTokens() {
    document.querySelectorAll('form').forEach(ensureFormToken);
  }

  document.addEventListener('DOMContentLoaded', injectAllFormTokens);

  var nativeFetch = window.fetch.bind(window);
  window.fetch = function (url, options) {
    options = options || {};
    var method = (options.method || 'GET').toUpperCase();
    if (method !== 'GET' && method !== 'HEAD' && method !== 'OPTIONS') {
      var token = csrfToken();
      if (token) {
        if (options.body instanceof FormData) {
          if (!options.body.has('csrf_token')) {
            options.body.append('csrf_token', token);
          }
        } else {
          options.headers = options.headers || {};
          if (options.headers instanceof Headers) {
            if (!options.headers.has('X-CSRFToken')) {
              options.headers.set('X-CSRFToken', token);
            }
          } else {
            if (!options.headers['X-CSRFToken']) {
              options.headers['X-CSRFToken'] = token;
            }
          }
        }
      }
    }
    return nativeFetch(url, options);
  };

  window.hrmsCsrfToken = csrfToken;
  window.hrmsEnsureFormCsrf = ensureFormToken;
})();
