/* global document */
// The htmx error banner. htmx-config does not swap 4xx/5xx bodies (views/layout.ejs), so without this a
// failed htmx save (a stale CSRF token's 403, a 500) changes nothing on screen. Loaded from /admin/public,
// the one same-origin path the admin CSP's script-src allows; the message is set as text, never HTML.
'use strict';

const MAX_MESSAGE_LENGTH = 300;
const banner = () => document.getElementById('htmx-error');

function show(text) {
  const el = banner();
  if (!el) return;
  el.textContent = text;
  el.hidden = false;
}

/** The admin error handler answers with vetted plain text; anything else gets fixed text. */
function messageOf(xhr) {
  const type = xhr.getResponseHeader('content-type') || '';
  const body = (xhr.responseText || '').trim();
  if (type.startsWith('text/plain') && body && body.length <= MAX_MESSAGE_LENGTH) return body;
  return `The request failed (HTTP ${xhr.status}). Reload the page and try again.`;
}

document.addEventListener('htmx:responseError', (event) => show(messageOf(event.detail.xhr)));
document.addEventListener('htmx:sendError', () =>
  show('The server could not be reached. Check your connection and try again.'),
);
document.addEventListener('htmx:beforeRequest', () => {
  const el = banner();
  if (!el) return;
  el.hidden = true;
  el.textContent = '';
});

// The CSP allows no inline handlers, so behaviour hangs off data attributes here.
// `js` on <html> lets the narrow-screen rail hide its links until the menu button opens them (shell.css).
if (document.documentElement) document.documentElement.classList.add('js');

// The rail's menu button (under 900px): flips aria-expanded, which shell.css reads to show the links.
document.addEventListener('click', (event) => {
  const button = event.target && event.target.closest ? event.target.closest('[data-menu]') : null;
  if (!button) return;
  button.setAttribute('aria-expanded', button.getAttribute('aria-expanded') === 'true' ? 'false' : 'true');
});
