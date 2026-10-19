import { App } from '@modelcontextprotocol/ext-apps';
import { providerLogo } from './provider-logos.js';

const app = new App({ name: 'OpenRouter Sidecar settings', version: '1.0.0' }, {}, { autoResize: false });
const el = id => document.getElementById(id);
const string = value => typeof value === 'string' ? value : '';
const keys = ['general', 'google', 'openai', 'x-ai', 'open_weight'];
const lastStep = document.querySelectorAll('[data-panel]').length - 1;
function eligibleModel(model) {
  if (typeof model?.id !== 'string' || !Number.isInteger(model.created)) return false;
  const id = model.id.toLowerCase();
  if (/(?:^|[^a-z0-9])batch(?:$|[^a-z0-9])/i.test(`${id} ${string(model.name)}`)) return false;
  const family = id.startsWith('google/') ? 'google' : id.startsWith('openai/') ? 'openai'
    : /^x-ai\/grok(?:[-\d.:/]|$)/.test(id) ? 'x-ai'
    : /^(?:z-ai|thudm)\/glm(?:[-\d./:]|$)|^qwen\/qwen(?:[-\d./:]|$)|^moonshotai\/kimi(?:[-\d./:]|$)|^minimax\/minimax(?:[-\d./:]|$)|^deepseek\/deepseek(?:[-\d./:]|$)/.test(id) ? 'open_weight' : null;
  const now = new Date();
  const cutoff = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - 6, 1, now.getUTCHours(), now.getUTCMinutes(), now.getUTCSeconds()));
  const lastDay = new Date(Date.UTC(cutoff.getUTCFullYear(), cutoff.getUTCMonth() + 1, 0)).getUTCDate();
  cutoff.setUTCDate(Math.min(now.getUTCDate(), lastDay));
  return family !== null && model.family === family && model.created >= cutoff.getTime() / 1000 && model.created <= now.getTime() / 1000;
}
let connected = false, disposed = false, loading = false, saving = false, step = 0;
let current = null, edits = {}, generation = 0, loaded = false;
let controller = new AbortController();
const timers = new Set();
const canTools = () => connected && !!app.getHostCapabilities()?.serverTools;
const clean = () => Object.keys(edits).length === 0;
const chosen = key => Object.hasOwn(edits, key) ? edits[key] : key === 'general' ? current?.general_default ?? '' : current?.family_defaults?.[key] ?? '';

// SDK autoResize observes root/body and temporarily mutates root height.
// Measure intrinsic main content instead, independently of host viewport height.
let sizeFrame = 0, reportedHeight = 0;
function queueSize() {
  if (!connected || disposed || sizeFrame) return;
  sizeFrame = requestAnimationFrame(() => {
    sizeFrame = 0;
    if (!connected || disposed) return;
    const contentHeight = Math.ceil(document.querySelector('main').getBoundingClientRect().height);
    const height = app.getHostContext()?.displayMode === 'fullscreen' ? contentHeight : Math.min(620, contentHeight);
    if (height <= 0 || height === reportedHeight) return;
    reportedHeight = height;
    // The host owns width; reporting viewport width can cause scrollbar loops.
    void app.sendSizeChanged({ height }).catch(() => {});
  });
}
const sizeObserver = new ResizeObserver(queueSize);
sizeObserver.observe(document.querySelector('main'));

function cancel() {
  for (const timer of timers) clearTimeout(timer);
  timers.clear(); controller.abort(); controller = new AbortController(); generation++;
}
function block(message) {
  cancel(); current = null; edits = {}; loaded = false;
  for (const key of keys) {
    el(key).replaceChildren(); el(`${key}-info`).textContent = ''; el(`${key}-extra`).textContent = '';
    el(`${key}-details`).hidden = true; el(`${key}-details`).open = false;
  }
  el('workspace').textContent = 'Sign-in or workspace access needs confirmation.';
  el('save-status').textContent = ''; el('finish-status').textContent = ''; el('status').textContent = '';
  el('error').textContent = message; el('login').hidden = false; update();
}
function theme(context) {
  if (context?.theme === 'light' || context?.theme === 'dark') document.documentElement.dataset.theme = context.theme;
}
function navigate(next) {
  step = next;
  document.querySelectorAll('[data-panel]').forEach(panel => panel.hidden = Number(panel.dataset.panel) !== step);
  document.querySelectorAll('[data-step]').forEach(button => {
    if (Number(button.dataset.step) === step) button.setAttribute('aria-current', 'step'); else button.removeAttribute('aria-current');
  });
  el('back').hidden = step === 0; el('next').hidden = step === lastStep; el('finish').hidden = step !== lastStep;
  update();
}
function update() {
  const ready = canTools() && !!current && !disposed && !loading && !saving;
  el('refresh').disabled = !canTools() || disposed || loading || saving;
  el('login').disabled = !canTools() || disposed || loading || saving;
  for (const key of keys) el(key).disabled = !ready;
  el('retry-save').hidden = clean() || saving;
  el('retry-save').disabled = !ready || clean();
  const needsModel = !current?.models.some(model => model.id === chosen('general'));
  document.querySelector('[data-step="1"]').classList.toggle('needs-choice', needsModel);
  el('model-task').textContent = needsModel ? 'Choose a model' : 'Models';
  el('model-task').hidden = !needsModel;
  el('general-required').hidden = !needsModel;
  const brands = keys.filter(key => key !== 'general' && chosen(key)).length;
  el('brand-count').textContent = brands ? `${brands} set` : 'Optional';
  el('next').disabled = step === 1 && (!ready || !clean() || needsModel);
  el('finish').disabled = !ready || !clean() || current.onboarding_completed === true
    || needsModel || keys.some(key => chosen(key) && !current.models.some(model => model.id === chosen(key)));
}
function modelInfo(model) {
  if (!model) return 'No default selected.';
  const price = model.pricing ?? {};
  const safePrice = (value, multiplier = 1) => {
    const number = typeof value === 'string' && /^\d+(?:\.\d+)?(?:e[+-]?\d+)?$/i.test(value) ? Number(value) : typeof value === 'number' ? value : NaN;
    return Number.isFinite(number * multiplier) && number >= 0
      ? `$${(number * multiplier).toLocaleString('en-US', { maximumSignificantDigits: 8 })}` : 'unavailable';
  };
  const context = Number.isInteger(model.context_length) && model.context_length > 0 ? model.context_length.toLocaleString('en-US', { useGrouping: false }) : 'unavailable';
  return `${safePrice(price.prompt, 1e6)} input · ${safePrice(price.completion, 1e6)} output / 1M tokens · Context: ${context} tokens`
    + (price.image !== undefined ? ` · Image: ${safePrice(price.image)} / image` : '')
    + (price.request !== undefined ? ` · Request: ${safePrice(price.request)} / request` : '')
    + Object.entries(price).filter(([key]) => ['internal_reasoning', 'input_cache_read', 'input_cache_write'].includes(key))
      .map(([key, value]) => ` · ${key.replaceAll('_', ' ')}: ${safePrice(value, 1e6)} / 1M tokens`).join('');
}
function options(select, models, selected) {
  select.replaceChildren(new Option('No default selected', ''));
  for (const model of models) select.add(new Option(string(model.name) || model.id, model.id));
  select.value = models.some(model => model.id === selected) ? selected : '';
}
function showModelInfo(key, model, unavailable = '') {
  const mark = el(`${key}-logo`);
  providerLogo(mark, model?.id || (key === 'general' || key === 'open_weight' ? '' : `${key}/`));
  const node = el(`${key}-info`);
  node.hidden = !chosen(key);
  node.replaceChildren();
  const details = el(`${key}-details`), extra = el(`${key}-extra`);
  details.hidden = !model; extra.replaceChildren();
  if (node.dataset.model !== model?.id) details.open = false;
  node.dataset.model = model?.id ?? '';
  if (!model || unavailable) { node.textContent = unavailable || modelInfo(model); return; }
  const parts = modelInfo(model).split(' · ');
  const identifier = document.createElement('span'); identifier.className = 'price-context'; identifier.textContent = model.id; extra.append(identifier);
  for (const [index, part] of parts.entries()) {
    const span = document.createElement('span');
    span.className = index < 2 ? 'price-metric' : 'price-context';
    const match = index < 2 && part.match(/^(\$[\d,.]+)(.*)$/);
    if (match) {
      const strong = document.createElement('strong'); strong.textContent = match[1];
      span.append(strong, document.createTextNode(match[2]));
    } else span.textContent = part;
    const target = index < 2 ? node : extra;
    if (index) target.append(document.createTextNode(' '));
    target.append(span);
  }
}
function render() {
  for (const key of keys) {
    const available = current.models.filter(model => key === 'general' || model.family === key);
    options(el(key), available, chosen(key));
    const selected = chosen(key), model = available.find(model => model.id === selected);
    showModelInfo(key, model, selected && !model ? 'Saved default is no longer eligible. Choose a current model.' : '');
  }
  el('workspace').textContent = `Signed in · Workspace: ${string(current.workspace_id) || 'configured workspace'}${string(current.cohort) ? ` · Cohort: ${current.cohort}` : ''}`;
  el('status').textContent = '';
  if (keys.some(key => key !== 'general' && chosen(key) && !current.models.some(model => model.id === chosen(key)))) el('brand-defaults').open = true;
  el('login').hidden = true;
  if (current.onboarding_completed) el('finish-status').textContent = 'Onboarding completed. You can edit settings any time.';
  if (!el('warning').textContent && keys.some(key => chosen(key) && !current.models.some(model => model.id === chosen(key)))) {
    el('warning').textContent = 'A saved default is no longer eligible. Choose a current model.';
  }
  if (current.models.length === 0) el('warning').textContent = 'No recent eligible advisor models are available. Refresh the catalog to check again.';
  update();
}
function accept(data) {
  if (data?.signed_in === false) {
    block(''); loaded = true;
    el('workspace').textContent = `Signed out · Workspace: ${string(data.workspace_id) || 'configured workspace'}${string(data.cohort) ? ` · Cohort: ${data.cohort}` : ''}`;
    el('warning').textContent = 'Sign in to choose your models, then choose Refresh.';
    return;
  }
  if (!data || typeof data.settings_id !== 'string' || !data.settings_id || !Number.isInteger(data.revision) || !Array.isArray(data.models)) {
    block('The host returned invalid settings. Refresh or reopen Sidecar settings.'); return;
  }
  const changed = current && current.settings_id !== data.settings_id;
  if (current && !changed && data.revision < current.revision) return;
  const revisionChanged = current && !changed && current.revision !== data.revision;
  const stale = current && current.revision !== data.revision && !clean();
  if (changed) { cancel(); edits = {}; el('save-status').textContent = ''; el('finish-status').textContent = ''; }
  else if (revisionChanged) cancel();
  current = { ...data, models: data.models.filter(eligibleModel) };
  loaded = true; el('error').textContent = '';
  el('warning').textContent = changed ? 'Account or workspace changed. Stale choices were cleared.'
    : stale ? 'Preferences changed elsewhere. Review your choice, then retry the save.' : !clean() ? 'Your choice has not been saved. Review it, then retry.' : '';
  render();
}
async function request(name, args = {}) {
  const signal = controller.signal;
  signal.throwIfAborted();
  return app.callServerTool({ name, arguments: args }, { signal, timeout: 15000 });
}
async function load() {
  if (!canTools() || disposed || loading) return;
  loading = true; const token = generation; update();
  try {
    const result = await request('get_sidecar_settings');
    if (disposed || token !== generation) return;
    if (result.isError) { block('Sign in or confirm your workspace, then choose Refresh.'); return; }
    accept(result._meta?.sidecarSettings);
  } catch {
    if (!disposed && token === generation) block('Unable to load settings. Sign in if needed, then choose Refresh.');
  } finally { loading = false; update(); }
}
async function save(completion = false) {
  if (!current || saving || !canTools() || disposed || loading || (completion && !clean())) return;
  const token = generation, id = current.settings_id;
  const args = { settings_id: id, expected_revision: current.revision };
  if (completion) args.onboarding_completed = true;
  else {
    if (Object.hasOwn(edits, 'general')) args.general_default = edits.general;
    const families = Object.fromEntries(Object.entries(edits).filter(([key]) => key !== 'general'));
    if (Object.keys(families).length) args.family_defaults = families;
    if (clean()) return;
  }
  saving = true; el('error').textContent = ''; el('save-status').textContent = completion ? '' : 'Saving…'; update();
  try {
    const result = await request('set_sidecar_preferences', args);
    if (disposed || token !== generation || current?.settings_id !== id) return;
    if (result.isError) {
      // Refresh authoritative ownership; only the same settings ID keeps edits.
      await load();
      if (!disposed && current?.settings_id === id) { el('error').textContent = 'Your choice was not saved. Review the refreshed defaults, then choose Retry save.'; el('save-status').textContent = 'Not saved'; }
      return;
    }
    const prefs = result.structuredContent;
    if (!prefs || prefs.settings_id !== id || !Number.isInteger(prefs.revision)) { block('Save was not confirmed. Refresh before making changes.'); return; }
    if (prefs.revision !== current.revision) cancel();
    edits = {}; current = { ...current, ...prefs }; el('warning').textContent = '';
    if (completion) el('finish-status').textContent = 'Onboarding completed. Reopen settings any time.';
    else el('save-status').textContent = 'Saved defaults. Existing chats keep their original models.';
    render();
  } catch {
    if (!disposed && token === generation) { el('error').textContent = 'Save could not be confirmed. Refresh to check the saved choice before retrying.'; el('save-status').textContent = 'Save not confirmed'; }
  } finally { saving = false; update(); }
}
for (const key of keys) el(key).addEventListener('change', () => {
  if (!current || saving || loading || disposed) return;
  const base = key === 'general' ? current.general_default ?? '' : current.family_defaults?.[key] ?? '';
  if (el(key).value === base) delete edits[key]; else edits[key] = el(key).value;
  showModelInfo(key, current.models.find(model => model.id === el(key).value));
  el('save-status').textContent = ''; el('warning').textContent = ''; update(); void save();
});
function selectImageTab(index, focus = false) {
  const tabs = [...document.querySelectorAll('.image-tabs [role="tab"]')];
  tabs.forEach((tab, i) => {
    tab.setAttribute('aria-selected', String(i === index));
    tab.tabIndex = i === index ? 0 : -1;
    el(tab.getAttribute('aria-controls')).hidden = i !== index;
  });
  if (focus) tabs[index].focus();
  queueSize();
}
document.querySelectorAll('.image-tabs [role="tab"]').forEach((tab, index) => {
  tab.addEventListener('click', () => selectImageTab(index));
  tab.addEventListener('keydown', event => {
    const next = { ArrowRight: 1 - index, ArrowLeft: 1 - index, Home: 0, End: 1 }[event.key];
    if (next === undefined) return;
    event.preventDefault(); selectImageTab(next, true);
  });
});
el('image-compare').addEventListener('input', event => {
  document.querySelector('.edit-comparison').style.setProperty('--reveal', `${event.target.value}%`);
});
document.querySelectorAll('[data-step]').forEach(button => button.addEventListener('click', () => navigate(Number(button.dataset.step))));
el('next').addEventListener('click', () => navigate(Math.min(lastStep, step + 1)));
el('back').addEventListener('click', () => navigate(Math.max(0, step - 1)));
el('refresh').addEventListener('click', load); el('retry-save').addEventListener('click', () => save()); el('finish').addEventListener('click', () => save(true));
el('login').addEventListener('click', async () => {
  if (!canTools() || disposed) return;
  el('login').disabled = true;
  try { await request('auth_login'); if (!disposed) el('status').textContent = 'Complete sign-in, then choose Refresh to load your workspace.'; }
  catch { if (!disposed) el('error').textContent = 'Sign-in could not be opened. Use auth_login in the parent chat, then Refresh.'; }
  finally { update(); }
});
function dispose() { disposed = true; cancel(); sizeObserver.disconnect(); cancelAnimationFrame(sizeFrame); update(); }
app.ontoolresult = result => {
  if (disposed) return;
  const mode = result.structuredContent?.mode;
  if (mode === 'settings' || mode === 'onboarding') {
    el('title').textContent = mode === 'settings' ? 'Sidecar settings' : 'Set up Sidecar'; navigate(mode === 'settings' ? 1 : 0);
  }
  if (result._meta?.sidecarSettings) accept(result._meta.sidecarSettings);
  else if (!loaded && connected && !loading) void load();
};
app.onhostcontextchanged = theme;
app.onteardown = async () => { dispose(); return {}; };
window.addEventListener('pagehide', dispose, { once: true });
app.connect().then(() => {
  if (disposed) return;
  connected = true; theme(app.getHostContext()); update(); queueSize();
  if (!canTools()) { el('status').textContent = ''; el('error').textContent = 'This host does not support Sidecar app tools. Open Sidecar settings in a compatible host.'; return; }
  // Initial tool results arrive separately after connect; allow their full view.
  const timer = setTimeout(() => { timers.delete(timer); if (!disposed && !loaded && !loading) void load(); }, 25);
  timers.add(timer);
}).catch(() => { if (!disposed) block('Unable to connect to the host. Reopen Sidecar settings.'); });
