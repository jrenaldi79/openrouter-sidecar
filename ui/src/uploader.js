import { App } from '@modelcontextprotocol/ext-apps';

// Uploads go straight from this iframe to the Sidecar's own single-use PUT endpoint.
// The page never sees a bearer token: the host proxies the tool call, and the upload
// URL is the only credential.
const MAX_BYTES = 20 * 1024 * 1024;
const TYPES = new Set(['image/png', 'image/jpeg', 'image/webp']);
const ORIGIN = document.querySelector('meta[name="sidecar-origin"]')?.content ?? '';

const app = new App({ name: 'OpenRouter Sidecar image uploader', version: '1.0.0' }, {}, { autoResize: true });
const drop = document.getElementById('drop');
const input = document.getElementById('files');
const list = document.getElementById('uploads');
const uploaded = [];

class Retryable extends Error {}

function textElement(tag, text, className) {
  const element = document.createElement(tag);
  element.textContent = text;
  if (className) element.className = className;
  return element;
}

function setState(item, text, kind = '') {
  item.state.replaceChildren(text);
  item.state.className = `state ${kind}`.trim();
  item.retry?.remove();
  item.retry = null;
}

function problem(file) {
  if (!TYPES.has(file.type)) return 'Not uploaded: only PNG, JPEG or WebP images are accepted.';
  if (file.size > MAX_BYTES) return 'Not uploaded: the file is larger than 20 MB.';
  if (file.size === 0) return 'Not uploaded: the file is empty.';
  return null;
}

async function uploadUrl(file) {
  const ticket = await app.callServerTool({ name: 'create_image_upload',
    arguments: { filename: file.name, mime: file.type, size_bytes: file.size } });
  if (ticket.isError) {
    const reason = (ticket.content ?? []).find(item => item.type === 'text')?.text;
    throw new Error(reason || 'The Sidecar refused the upload.');
  }
  const url = ticket.structuredContent?.upload_url;
  if (typeof url !== 'string' || !ORIGIN || !url.startsWith(`${ORIGIN}/uploads/`)) {
    throw new Error('Not uploaded: the Sidecar returned an unexpected upload link.');
  }
  return url;
}

async function put(url, file) {
  let response;
  try {
    response = await fetch(url, { method: 'PUT', body: file, headers: { 'Content-Type': file.type } });
  } catch {
    throw new Retryable('Network error: the file was not uploaded. Check your connection and retry.');
  }
  const body = await response.json().catch(() => ({}));
  if (response.status >= 500) throw new Retryable(`The Sidecar had a problem (${response.status}); retry.`);
  if (!response.ok || typeof body.asset_id !== 'string') throw new Error(body.error || `Upload failed (${response.status}).`);
  return body;
}

async function tellModel() {
  const lines = uploaded.map(a => `- "${a.filename}" (${a.width}×${a.height}): image asset ${a.asset_id}`);
  await app.updateModelContext({ content: [{ type: 'text', text:
    `The user uploaded these images to the Sidecar:\n${lines.join('\n')}\n`
    + 'Use the asset ids with edit_image (in images, or as mask_id).' }] });
}

async function upload(item) {
  setState(item, 'Uploading…');
  try {
    const body = await put(await uploadUrl(item.file), item.file);
    uploaded.push({ ...body, filename: item.file.name });
    const id = textElement('code', body.asset_id);
    setState(item, 'Uploaded as ', 'done');
    item.state.append(id);
    await tellModel().catch(() => item.state.append(' (copy this id into the chat)'));
  } catch (error) {
    setState(item, error.message, 'error');
    if (error instanceof Retryable) {
      item.retry = textElement('button', 'Retry');
      item.retry.type = 'button';
      item.retry.setAttribute('aria-label', `Retry ${item.file.name}`);
      item.retry.addEventListener('click', () => upload(item), { once: true });
      item.row.append(item.retry);
    }
  }
}

async function addFiles(files) {
  for (const file of files) {
    const row = textElement('li', '');
    const item = { file, row, state: textElement('span', '', 'state'), retry: null };
    row.append(textElement('span', file.name, 'name'), item.state);
    list.append(row);
    const reason = problem(file);
    if (reason) setState(item, reason, 'error');
    else await upload(item);
  }
}

input.addEventListener('change', () => { addFiles([...input.files]); input.value = ''; });
drop.addEventListener('dragover', event => { event.preventDefault(); drop.classList.add('over'); });
drop.addEventListener('dragleave', () => drop.classList.remove('over'));
drop.addEventListener('drop', event => {
  event.preventDefault();
  drop.classList.remove('over');
  addFiles([...(event.dataTransfer?.files ?? [])]);
});

function setTheme(context) {
  if (context?.theme === 'light' || context?.theme === 'dark') document.documentElement.dataset.theme = context.theme;
}

app.onhostcontextchanged = setTheme;
app.connect().then(() => setTheme(app.getHostContext())).catch(() => {
  list.append(textElement('li', 'Unable to connect to the host; use create_image_upload instead.', 'state error'));
});
