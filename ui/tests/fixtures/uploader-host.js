import { readFile } from 'node:fs/promises';

export const ORIGIN = 'http://sidecar.test';
// The server substitutes its own origin into the resource, exactly like this.
const html = (await readFile(new URL('../../../src/openrouter_image_mcp/ui/uploader.html', import.meta.url), 'utf8')
  .catch(error => { if (error.code === 'ENOENT') return '<!doctype html><body></body>'; throw error; }))
  .replaceAll('__SIDECAR_ORIGIN__', ORIGIN);

export const ticket = (n = 1) => ({ content: [{ type: 'text', text: 'Upload with one PUT' }], structuredContent: {
  upload_id: `upl_${n}`, upload_url: `${ORIGIN}/uploads/upl_${n}.9999999999.sig`, expires_in: 600, max_bytes: 1 } });

// Serves the upload endpoint: `uploads` is a list of 'ok' | 'network' | {status, error}, used in order.
export async function routeUploads(page, uploads = ['ok']) {
  const seen = [];
  await page.route('**/*', async route => {
    const request = route.request();
    if (!request.url().startsWith(`${ORIGIN}/uploads/`)) return route.abort();
    const cors = { 'access-control-allow-origin': '*', 'access-control-allow-methods': 'PUT',
      'access-control-allow-headers': 'Content-Type' };
    if (request.method() === 'OPTIONS') return route.fulfill({ status: 204, headers: cors });
    const plan = uploads[Math.min(seen.length, uploads.length - 1)];
    seen.push({ url: request.url(), type: request.headers()['content-type'], size: request.postDataBuffer()?.length });
    if (plan === 'network') return route.abort('internetdisconnected');
    if (plan !== 'ok') return route.fulfill({ status: plan.status, headers: cors, json: { error: plan.error } });
    return route.fulfill({ status: 200, headers: cors, json: {
      asset_id: `img_${String(seen.length).padStart(32, '0')}`, width: 4, height: 3, mime: 'image/png', filename: 'room.png' } });
  });
  return seen;
}

export async function openUploader(page, { tickets = [ticket(1), ticket(2), ticket(3)] } = {}) {
  page.on('pageerror', error => console.error('Uploader error:', error.message));
  await page.setContent('<iframe title="Image uploader" sandbox="allow-scripts" style="width:600px;height:300px;border:0"></iframe>');
  await page.evaluate(({ html, tickets }) => {
    window.hostMessages = []; window.toolCount = 0;
    const iframe = document.querySelector('iframe');
    const reply = (id, value) => iframe.contentWindow.postMessage({ jsonrpc: '2.0', id, ...value }, '*');
    window.addEventListener('message', event => {
      if (event.source !== iframe.contentWindow) return;
      const message = event.data; window.hostMessages.push(message);
      if (message.method === 'ui/initialize') {
        reply(message.id, { result: { protocolVersion: '2026-01-26', hostInfo: { name: 'Offline uploader host', version: '1' },
          hostCapabilities: { serverTools: {}, updateModelContext: { text: {} } }, hostContext: { theme: 'light', displayMode: 'inline' } } });
      }
      if (message.method === 'ui/notifications/size-changed') iframe.style.height = `${message.params.height}px`;
      if (message.method === 'ui/update-model-context') reply(message.id, { result: {} });
      if (message.method === 'tools/call') reply(message.id, { result: tickets[Math.min(window.toolCount++, tickets.length - 1)] });
    });
    iframe.srcdoc = html;
  }, { html, tickets });
  const viewer = page.frameLocator('iframe');
  await page.waitForFunction(() => window.hostMessages.some(m => m.method === 'ui/notifications/initialized'));
  return viewer;
}

export const messages = (page, method) => page.evaluate(name => window.hostMessages.filter(m => m.method === name), method);
export const pngFile = (name = 'room.png', size = 64) => ({ name, mimeType: 'image/png', buffer: Buffer.alloc(size, 1) });
