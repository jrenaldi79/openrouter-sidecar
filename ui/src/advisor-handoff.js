// Sharing advisor text with Claude. The viewer never posts a chat message as
// the user (hosts ask the user to approve that). It adds model context when the
// host supports it; otherwise it copies the text, or shows it for manual copy.

// claude.ai keeps added context until the model checks the app, so point the user there.
export const shared = what => `Shared ${what}. Ask Claude to check the advisor viewer for it.`;
export const copied = what => `Copied ${what}. Paste it into the chat.`;
export const COPY_BLOCKED = 'Clipboard access is unavailable. Select and copy the text below, then paste it into the chat.';
export const SUMMARIZING = 'Summarizing… (uses OpenRouter credits)';

export const canAddContext = app => !!app.getHostCapabilities()?.updateModelContext?.text;

// The source header marks the text as another model's output, with its ids.
export function handoffText(prepared, body) {
  const suffix = prepared.summaryId ? `; summary ${prepared.summaryId}` : '';
  const partial = prepared.partial ? ' (partial result)' : '';
  return `Advisor ${prepared.kind}${partial} from ${prepared.model} (result ${prepared.sourceId}${suffix}):\n\n${body}`;
}

export async function addToContext(app, text, signal) {
  await app.updateModelContext({ content: [{ type: 'text', text }] }, { signal, timeout: 15000 });
}

export async function copyText(text) {
  if (!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable');
  await navigator.clipboard.writeText(text);
}

// Copies `text`; when the clipboard is blocked (common in sandboxed frames),
// shows it in the read-only box for manual copy instead.
export async function copyOrShow(element, text, what) {
  try {
    await copyText(text);
    return copied(what);
  } catch {
    element('copy-text').value = text;
    element('copy-section').hidden = false;
    element('copy-text').focus();
    element('copy-text').select();
    return COPY_BLOCKED;
  }
}

// The "More" disclosure closes after a choice, on Escape and on outside clicks.
export function wireMenu(menu) {
  menu.addEventListener('click', event => { if (event.target.closest('button')) menu.open = false; });
  menu.addEventListener('keydown', event => {
    if (event.key !== 'Escape' || !menu.open) return;
    menu.open = false;
    menu.querySelector('summary').focus();
  });
  document.addEventListener('click', event => { if (menu.open && !menu.contains(event.target)) menu.open = false; });
}
