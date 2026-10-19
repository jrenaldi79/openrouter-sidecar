import { test, expect } from '@playwright/test';
import { readFile } from 'node:fs/promises';

// Test the distributed artifact, with the real SDK and an iframe host.
const html = await readFile(new URL('../../src/openrouter_image_mcp/ui/preview.html', import.meta.url), 'utf8')
  .catch(error => { if (error.code === 'ENOENT') return '<!doctype html><body></body>'; throw error; });
const jpeg = `data:image/jpeg;base64,${(await readFile(new URL('./fixtures/grid-jpeg.txt', import.meta.url), 'utf8')).trim()}`;
const sample = (overrides = {}) => ({
  images: [{ filename: 'render.png', path: 'C:\\images\\render.png' }],
  model: 'example/image-model', provider: 'Example provider', callCostUsd: 0.01234,
  usageLine: 'Cost: $0.01234 (reported; estimates may differ from final billing).', notes: [], failures: [],
  ...overrides,
});
// Previews travel in the result's _meta (index-aligned with images), never in structuredContent.
const toolResult = (structuredContent, extra = {}) => ({ content: [], structuredContent,
  _meta: { sidecarPreviews: (structuredContent?.images ?? []).map(() => jpeg) }, ...extra });

async function openViewer(page, { resultAtInitialize, theme = 'light', tools = {}, downloadDenied = false } = {}) {
  const requests = [];
  page.on('pageerror', error => console.error('Viewer error:', error.message));
  page.on('console', message => { if (message.type() === 'error') console.error('Viewer console:', message.text()); });
  await page.route('**/*', route => { requests.push(route.request().url()); return route.abort(); });
  await page.setContent('<iframe title="Image preview" sandbox="allow-scripts" style="width:700px;height:100px;border:0"></iframe>');
  await page.evaluate(({ html, resultAtInitialize, theme, tools, downloadDenied }) => {
    window.hostMessages = [];
    const reply = (id, result) => document.querySelector('iframe').contentWindow.postMessage({ jsonrpc: '2.0', id, result }, '*');
    const iframe = document.querySelector('iframe');
    window.notify = (method, params) => iframe.contentWindow.postMessage({ jsonrpc: '2.0', method, params }, '*');
    window.addEventListener('message', event => {
      if (event.source !== iframe.contentWindow) return;
      window.hostMessages.push(event.data);
      if (event.data.method === 'ui/initialize') {
        iframe.contentWindow.postMessage({ jsonrpc: '2.0', id: event.data.id, result: {
          protocolVersion: '2026-01-26', hostInfo: { name: 'Offline test host', version: '1.0.0' },
          hostCapabilities: {}, hostContext: { theme, displayMode: 'inline' },
        } }, '*');
        if (resultAtInitialize) window.notify('ui/notifications/tool-result', resultAtInitialize);
      }
      if (event.data.method === 'ui/notifications/size-changed') {
        iframe.style.height = `${event.data.params.height}px`;
      }
      if (event.data.method === 'tools/call') reply(event.data.id, tools[event.data.params.name] ?? { isError: true, content: [] });
      if (event.data.method === 'ui/download-file') reply(event.data.id, downloadDenied ? { isError: true } : {});
      if (event.data.method === 'ui/open-link') reply(event.data.id, {});
    });
    iframe.srcdoc = html;
  }, { html, resultAtInitialize, theme, tools, downloadDenied });
  return { viewer: page.frameLocator('iframe'), requests };
}

async function result(page, structuredContent, extra = {}) {
  await expect.poll(() => page.evaluate(() => window.hostMessages.some(m => m.method === 'ui/notifications/initialized'))).toBe(true);
  await page.evaluate(params => window.notify('ui/notifications/tool-result', params), toolResult(structuredContent, extra));
}

test('initializes and waits for an image result', async ({ page }) => {
  const { viewer } = await openViewer(page);
  await expect(viewer.getByRole('status')).toHaveText('Waiting for image results…');
  await expect.poll(() => page.evaluate(() => window.hostMessages.some(m => m.method === 'ui/notifications/initialized'))).toBe(true);
});

test('receives immediate results, renders JPEG and reports its new size', async ({ page }) => {
  const { viewer, requests } = await openViewer(page, { resultAtInitialize: toolResult(sample()) });
  await expect(viewer.getByRole('img', { name: 'render.png' })).toBeVisible();
  await expect.poll(() => viewer.getByRole('img').evaluate(img => img.complete && img.naturalWidth)).toBe(40);
  await expect(viewer.getByText('C:\\images\\render.png', { exact: true })).toBeVisible();
  await expect(viewer.getByText('Example provider', { exact: true })).toBeVisible();
  await expect(viewer.getByText(sample().usageLine, { exact: true })).toBeVisible();
  await expect.poll(() => page.evaluate(() => window.hostMessages.filter(m => m.method === 'ui/notifications/size-changed').some(m => m.params.height > 150))).toBe(true);
  expect(requests).toEqual([]);
  await expect.poll(() => page.locator('iframe').evaluate(frame => frame.clientHeight)).toBeGreaterThan(150);
  await page.screenshot({ path: 'test-results/gallery.png' });
});

test('shows call total once across multiple images', async ({ page }) => {
  const { viewer } = await openViewer(page);
  await result(page, sample({ usageLine: '', images: [sample().images[0], { ...sample().images[0], filename: 'second.png' }] }));
  await expect(viewer.getByText('Call total: $0.01234', { exact: true })).toHaveCount(1);
  await expect(viewer.getByRole('img')).toHaveCount(2);
});

test('distinguishes unavailable cost from free remasking', async ({ page }) => {
  const { viewer } = await openViewer(page);
  await result(page, sample({ callCostUsd: null, provider: null, usageLine: '' }));
  await expect(viewer.getByText('Call total: unavailable', { exact: true })).toBeVisible();
  await result(page, sample({ callCostUsd: 0, model: 'Local re-blend', provider: null, usageLine: '' }));
  await expect(viewer.getByText('Call total: free ($0)', { exact: true })).toBeVisible();
  await expect(viewer.getByText('Local re-blend', { exact: true })).toBeVisible();
  await expect(viewer.getByText('Call total: unavailable', { exact: true })).toHaveCount(0);
});

test('keeps the original usage and cost disclosure without duplicating a total', async ({ page }) => {
  const { viewer } = await openViewer(page);
  await result(page, sample());
  await expect(viewer.getByText(sample().usageLine, { exact: true })).toBeVisible();
  await expect(viewer.getByText('Call total: $0.01234', { exact: true })).toHaveCount(0);
  await result(page, sample({ callCostUsd: null, usageLine: 'Cost unavailable' }));
  await expect(viewer.getByText('Cost unavailable', { exact: true })).toBeVisible();
  await result(page, sample({ callCostUsd: 0, model: 'Local re-blend', usageLine: 'Cost: $0.00 (local re-blend, nothing uploaded)' }));
  await expect(viewer.getByText('Cost: $0.00 (local re-blend, nothing uploaded)', { exact: true })).toBeVisible();
});

test('preserves filename and path without a preview', async ({ page }) => {
  const { viewer } = await openViewer(page);
  await result(page, sample({ images: [{ filename: 'vector.svg', path: 'C:\\images\\vector.svg' }] }), { _meta: { sidecarPreviews: [null] } });
  await expect(viewer.getByText('vector.svg', { exact: true })).toBeVisible();
  await expect(viewer.getByText('C:\\images\\vector.svg', { exact: true })).toBeVisible();
  await expect(viewer.getByText('Preview unavailable', { exact: true })).toBeVisible();
  await expect(viewer.getByRole('img')).toHaveCount(0);
});

test('shows partial results, notes, and failures as text', async ({ page }) => {
  const { viewer } = await openViewer(page);
  await result(page, sample({ notes: ['Preview reduced to fit.'], failures: ['Image 2 could not be saved.'] }));
  await expect(viewer.getByRole('status')).toHaveText('1 image saved · Partial result');
  await expect(viewer.getByRole('img')).toHaveCount(1);
  await expect(viewer.getByText('Preview reduced to fit.', { exact: true })).toBeVisible();
  await expect(viewer.getByText('Image 2 could not be saved.', { exact: true })).toBeVisible();
});

test('shows ordinary tool error text without structured results', async ({ page }) => {
  const { viewer } = await openViewer(page);
  await result(page, undefined, { isError: true, content: [{ type: 'text', text: 'No image: signed out.' }] });
  await expect(viewer.getByRole('status')).toHaveText('Image request failed');
  await expect(viewer.getByText('No image: signed out.', { exact: true })).toBeVisible();
});

test('shows fallback text on a result with no images', async ({ page }) => {
  const { viewer } = await openViewer(page);
  await result(page, sample({ images: [] }), { content: [{ type: 'text', text: 'Provider returned no images.' }] });
  await expect(viewer.getByRole('status')).toHaveText('No images returned');
  await expect(viewer.getByText('Provider returned no images.', { exact: true })).toBeVisible();
});

test('renders hostile fields literally and never requests hostile URLs', async ({ page }) => {
  const { viewer, requests } = await openViewer(page);
  const hostile = '<img src="https://evil.test/pixel" onerror="window.pwned=true">';
  await result(page, sample({
    images: Array.from({ length: 4 }, () => ({ filename: hostile, path: hostile })),
    model: hostile, provider: hostile, usageLine: hostile, notes: [hostile], failures: [hostile],
  }), { _meta: { sidecarPreviews: ['https://evil.test/image.jpg', 'javascript:alert(1)', 'data:image/svg+xml;base64,PHN2Zz4=', 'data:image/jpeg;base64,!!!'] } });
  await expect(viewer.getByText(hostile, { exact: true }).first()).toBeVisible();
  await expect(viewer.getByRole('img')).toHaveCount(0);
  await expect(viewer.locator('a, svg')).toHaveCount(0);
  expect(await viewer.locator('body').evaluate(() => window.pwned)).toBeUndefined();
  expect(requests).toEqual([]);
});

test('follows host theme changes', async ({ page }) => {
  const { viewer } = await openViewer(page, { theme: 'dark' });
  await expect(viewer.locator('html')).toHaveAttribute('data-theme', 'dark');
  await page.evaluate(() => window.notify('ui/notifications/host-context-changed', { theme: 'light' }));
  await expect(viewer.locator('html')).toHaveAttribute('data-theme', 'light');
});

const hosted = () => sample({ images: [{ filename: 'barn__sunburst_1.png', assetId: 'img_' + 'a'.repeat(32),
  downloadUrl: 'https://sidecar.example/assets/stale', metadataUrl: 'https://sidecar.example/assets/stale-json' }] });
const fresh = { content: [], structuredContent: { asset_id: 'img_' + 'a'.repeat(32), filename: 'barn__sunburst_1.png',
  image_url: 'https://sidecar.example/assets/fresh', metadata_url: 'https://sidecar.example/assets/fresh-json', expires_in: 86400 } };
async function clickDownload(page, viewer) {
  // Let the preview load first: its arrival reflows the figure under the button. The
  // button sits near the bottom of a 720px page, so give the page room to click it.
  await page.setViewportSize({ width: 1280, height: 1200 });
  await expect.poll(() => viewer.getByRole('img').evaluate(img => img.complete && img.naturalWidth)).toBe(40);
  await viewer.getByRole('button', { name: 'Download barn__sunburst_1.png' }).click();
}
const sent = (page, method) => page.evaluate(name => window.hostMessages.filter(m => m.method === name).map(m => m.params), method);

test('hosted results show the asset id and download through the host with a fresh link', async ({ page }) => {
  const { viewer, requests } = await openViewer(page, { resultAtInitialize: toolResult(hosted()),
    tools: { get_image_download: fresh } });
  await expect(viewer.getByText('img_' + 'a'.repeat(32))).toBeVisible();
  await clickDownload(page, viewer);
  await expect.poll(() => sent(page, 'ui/download-file')).toHaveLength(1);
  expect((await sent(page, 'tools/call'))[0]).toMatchObject({ name: 'get_image_download', arguments: { asset_id: 'img_' + 'a'.repeat(32) } });
  const [download] = await sent(page, 'ui/download-file');
  expect(download.contents).toEqual([{ type: 'resource_link', uri: 'https://sidecar.example/assets/fresh',
    name: 'barn__sunburst_1.png', mimeType: 'image/png' }]);
  expect(requests.filter(url => url.includes('sidecar.example'))).toEqual([]);  // the iframe never fetches
});

test('falls back to opening the link when the host declines the download', async ({ page }) => {
  const { viewer } = await openViewer(page, { resultAtInitialize: toolResult(hosted()),
    tools: { get_image_download: fresh }, downloadDenied: true });
  await clickDownload(page, viewer);
  await expect.poll(() => sent(page, 'ui/open-link')).toEqual([{ url: 'https://sidecar.example/assets/fresh' }]);
});

test('shows an error when a fresh link cannot be had', async ({ page }) => {
  const { viewer } = await openViewer(page, { resultAtInitialize: toolResult(hosted()) });
  await clickDownload(page, viewer);
  await expect(viewer.getByText(/Download unavailable/)).toBeVisible();
  expect(await sent(page, 'ui/download-file')).toEqual([]);
});

test('local results keep the path and have no download button', async ({ page }) => {
  const { viewer } = await openViewer(page, { resultAtInitialize: toolResult(sample()) });
  await expect(viewer.getByText('C:\\images\\render.png')).toBeVisible();
  await expect(viewer.getByRole('button')).toHaveCount(0);
});

test('reads previews only from _meta, never from structuredContent', async ({ page }) => {
  const { viewer } = await openViewer(page);
  await result(page, sample({ images: [{ dataUri: jpeg, filename: 'legacy.png', path: 'C:\\images\\legacy.png' }] }), { _meta: {} });
  await expect(viewer.getByText('legacy.png', { exact: true })).toBeVisible();
  await expect(viewer.getByText('Preview unavailable', { exact: true })).toBeVisible();
  await expect(viewer.getByRole('img')).toHaveCount(0);
});

test('matches previews to images by index and tolerates malformed lists', async ({ page }) => {
  const { viewer } = await openViewer(page);
  const two = sample({ images: [{ filename: 'first.svg', path: 'a' }, { filename: 'second.png', path: 'b' }] });
  await result(page, two, { _meta: { sidecarPreviews: [null, jpeg] } });
  await expect(viewer.getByRole('img', { name: 'second.png' })).toBeVisible();
  await expect(viewer.getByRole('img')).toHaveCount(1);
  await result(page, two, { _meta: { sidecarPreviews: { 0: jpeg, 1: jpeg } } });
  await expect(viewer.getByText('Preview unavailable', { exact: true })).toHaveCount(2);
  await result(page, two, { _meta: { sidecarPreviews: [jpeg] } });
  await expect(viewer.getByRole('img', { name: 'first.svg' })).toBeVisible();
  await expect(viewer.getByText('Preview unavailable', { exact: true })).toHaveCount(1);
});

// claude.ai delivers tool results to the app without the result's _meta. The JPEG
// previews still arrive as the result's image content blocks, which the model sees anyway.
const imageBlock = (mimeType = 'image/jpeg') => ({ type: 'image', mimeType, data: jpeg.slice('data:image/jpeg;base64,'.length) });
const textBlock = { type: 'text', text: 'Asset: img_…' };

test('renders previews from image content when the host drops _meta', async ({ page }) => {
  const { viewer, requests } = await openViewer(page);
  await result(page, sample(), { _meta: undefined, content: [textBlock, imageBlock()] });
  await expect(viewer.getByRole('img', { name: 'render.png' })).toBeVisible();
  await expect.poll(() => viewer.getByRole('img').evaluate(img => img.complete && img.naturalWidth)).toBe(40);
  expect(requests).toEqual([]);
});

test('does not guess when image content does not line up with the images', async ({ page }) => {
  const { viewer } = await openViewer(page);
  const two = sample({ images: [{ filename: 'first.svg', path: 'a' }, { filename: 'second.png', path: 'b' }] });
  await result(page, two, { _meta: undefined, content: [textBlock, imageBlock()] });
  await expect(viewer.getByText('Preview unavailable', { exact: true })).toHaveCount(2);
  await expect(viewer.getByRole('img')).toHaveCount(0);
});

test('ignores image content that is not a JPEG preview', async ({ page }) => {
  const { viewer } = await openViewer(page);
  await result(page, sample(), { _meta: undefined, content: [textBlock, imageBlock('image/svg+xml')] });
  await expect(viewer.getByText('Preview unavailable', { exact: true })).toBeVisible();
  await expect(viewer.getByRole('img')).toHaveCount(0);
});
