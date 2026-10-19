import { App } from '@modelcontextprotocol/ext-apps';

const app = new App({ name: 'OpenRouter Sidecar image preview', version: '1.0.0' }, {}, { autoResize: true });
const status = document.getElementById('status');
const details = document.getElementById('details');
const gallery = document.getElementById('gallery');
const notes = document.getElementById('notes');
const failures = document.getElementById('failures');
const fallback = document.getElementById('fallback');

function textElement(tag, text, className) {
  const element = document.createElement(tag);
  element.textContent = typeof text === 'string' ? text : '';
  if (className) element.className = className;
  return element;
}

function textList(container, title, values) {
  if (!Array.isArray(values) || values.length === 0) return;
  container.append(textElement('h2', title));
  const list = document.createElement('ul');
  for (const value of values) list.append(textElement('li', value));
  container.append(list);
}

function costLine(cost) {
  if (cost === 0) return 'Call total: free ($0)';
  if (typeof cost !== 'number' || !Number.isFinite(cost) || cost < 0) return 'Call total: unavailable';
  return `Call total: $${cost}`;
}

const MIME_BY_EXT = { png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', webp: 'image/webp', svg: 'image/svg+xml' };

// Hosted assets: ask the server for a fresh signed link (stored ones expire after 24 h),
// then let the host download it. The iframe itself never fetches the image.
async function download(image, button, caption) {
  button.disabled = true;
  try {
    const result = await app.callServerTool({ name: 'get_image_download', arguments: { asset_id: image.assetId } });
    const url = result.structuredContent?.image_url;
    if (result.isError || typeof url !== 'string' || !/^https?:\/\//.test(url)) throw new Error('no link');
    const name = typeof image.filename === 'string' ? image.filename : `${image.assetId}.png`;
    const mimeType = MIME_BY_EXT[name.split('.').pop().toLowerCase()] ?? 'image/png';
    const saved = await app.downloadFile({ contents: [{ type: 'resource_link', uri: url, name, mimeType }] })
      .catch(() => ({ isError: true }));
    if (saved?.isError) await app.openLink({ url });
  } catch {
    caption.append(textElement('p', 'Download unavailable; ask for get_image_download in the chat.', 'path'));
  } finally {
    button.disabled = false;
  }
}

function downloadButton(image, caption) {
  const button = textElement('button', 'Download', 'download');
  button.type = 'button';
  button.setAttribute('aria-label', `Download ${typeof image.filename === 'string' ? image.filename : 'image'}`);
  button.addEventListener('click', () => download(image, button, caption));
  return button;
}

// Preview JPEGs arrive in the result's _meta, index-aligned with structuredContent.images:
// hosts may show structuredContent to the model, so it never carries image bytes.
// No remote URLs, SVG, or result HTML is ever inserted into the page.
const JPEG_DATA_URI = /^data:image\/jpeg;base64,(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/;
function previewAt(result, index) {
  const previews = result._meta?.sidecarPreviews;
  const uri = Array.isArray(previews) ? previews[index] : null;
  return typeof uri === 'string' && JPEG_DATA_URI.test(uri) && uri.length > 'data:image/jpeg;base64,'.length ? uri : null;
}

function setTheme(context) {
  if (context?.theme === 'light' || context?.theme === 'dark') document.documentElement.dataset.theme = context.theme;
}

function render(result) {
  for (const element of [details, gallery, notes, failures, fallback]) element.replaceChildren();
  const data = result.structuredContent;
  const images = Array.isArray(data?.images) ? data.images : [];
  const imageFailures = Array.isArray(data?.failures) ? data.failures : [];
  status.textContent = result.isError ? 'Image request failed'
    : images.length ? `${images.length} image${images.length === 1 ? '' : 's'} saved${imageFailures.length ? ' · Partial result' : ''}`
      : 'No images returned';

  if (data) {
    if (data.model) details.append(textElement('p', data.model));
    if (data.provider) details.append(textElement('p', data.provider));
    // The original disclosure includes cost and any estimation limitations.
    details.append(textElement('p', typeof data.usageLine === 'string' && data.usageLine.trim()
      ? data.usageLine : costLine(data.callCostUsd)));
    textList(notes, 'Notes', data.notes);
    textList(failures, 'Failures', imageFailures);
  }

  for (const [index, image] of images.entries()) {
    const figure = document.createElement('figure');
    const placeholder = () => textElement('p', 'Preview unavailable', 'no-preview');
    const source = previewAt(result, index);
    if (source) {
      const preview = document.createElement('img');
      preview.alt = typeof image?.filename === 'string' ? image.filename : 'Saved image';
      preview.addEventListener('error', () => preview.replaceWith(placeholder()), { once: true });
      preview.src = source;
      figure.append(preview);
    } else {
      figure.append(placeholder());
    }
    const caption = document.createElement('figcaption');
    const hostedAsset = typeof image?.assetId === 'string' && /^img_[0-9a-f]{32}$/.test(image.assetId);
    caption.append(textElement('p', image?.filename, 'filename'),
      textElement('p', hostedAsset ? image.assetId : image?.path, 'path'));
    if (hostedAsset) caption.append(downloadButton(image, caption));
    figure.append(caption);
    gallery.append(figure);
  }

  if (result.isError || images.length === 0) {
    for (const item of result.content ?? []) {
      if (item.type === 'text') fallback.append(textElement('p', item.text));
    }
  }
}

// Install handlers before connecting: a host can deliver results immediately.
app.ontoolresult = render;
app.onhostcontextchanged = setTheme;
app.connect().then(() => setTheme(app.getHostContext())).catch(() => {
  status.textContent = 'Unable to connect to the image preview host';
});
