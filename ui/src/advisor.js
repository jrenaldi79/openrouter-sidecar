import { App } from '@modelcontextprotocol/ext-apps';
import { renderMarkdown } from './markdown.js';
import { providerLogo } from './provider-logos.js';
import { createLiveView, createPoller } from './advisor-progress.js';
import { wireMenu } from './advisor-handoff.js';
import { createShare } from './advisor-share.js';

const app = new App({ name: 'OpenRouter Sidecar advisor result', version: '1.0.0' }, {}, { autoResize: true });
const element = id => document.getElementById(id);
const answer = element('answer');
const terminal = new Set(['completed', 'failed', 'interrupted']);
const states = new Set(['queued', 'running', ...terminal]);
const live = createLiveView(element);
const poller = createPoller();
let connected = false;
let disposed = false;
let blocked = false;
let generation = 0;
let controller = new AbortController();
let original = null;
let renderedAnswer = '';
let followUpBusy = false;
let fetchTail = Promise.resolve();

const string = value => typeof value === 'string' ? value : '';
const stateLabel = status => string(status).charAt(0).toUpperCase() + string(status).slice(1);
const costLabel = cost => typeof cost === 'number' && Number.isFinite(cost) && cost >= 0
  ? `Cost: $${cost}` : 'Cost: unavailable';
const canCallTools = () => connected && !!app.getHostCapabilities()?.serverTools;

function stopPolling() {
  poller.clear();
  controller.abort();
  controller = new AbortController();
  generation++;
}

function setTheme(context) {
  if (context?.theme === 'light' || context?.theme === 'dark') document.documentElement.dataset.theme = context.theme;
}

function updateButtons() {
  const sharing = share.busy();
  const following = followUpBusy || sharing;
  const canFollowUp = !disposed && !blocked && canCallTools() && original?.kind === 'message'
    && original.status === 'completed' && !!string(original.chat_id) && !following;
  element('follow-up').disabled = disposed || (!original && !blocked) || !canCallTools();
  element('follow-up').readOnly = !canFollowUp;
  element('follow-up-send').disabled = !canFollowUp || !element('follow-up').value.trim();
  const available = !disposed && !blocked && !followUpBusy && canCallTools() && !!string(original?.answer).trim();
  // Share only finished text: a completed answer, or an interrupted (labelled partial) one.
  share.update(available && ['completed', 'interrupted'].includes(original.status));
}

function showError(message) {
  element('error').textContent = message;
}

function suppressResult(message) {
  blocked = true;
  stopPolling();
  live.stop();
  original = null;
  followUpBusy = false;
  share.reset();
  answer.textContent = '';
  renderedAnswer = '';
  element('prompt').textContent = '';
  element('prompt-details').hidden = true;
  element('job-status').textContent = 'Result unavailable';
  showError(message);
  updateButtons();
}

function toolError(result) {
  return (result?.content ?? []).filter(item => item.type === 'text').map(item => string(item.text)).filter(Boolean).join('\n') || 'The host could not complete the request.';
}

function validateStatus(data, expectedId) {
  return data && typeof data.job_id === 'string' && data.job_id.length > 0 && states.has(data.status)
    && (!expectedId || data.job_id === expectedId);
}

const validResult = (data, id) => validateStatus(data, id) && typeof data.answer === 'string' && typeof data.prompt === 'string';

function renderOriginal(data) {
  original = data;
  element('result-title').textContent = string(data.title);
  element('model').textContent = string(data.model);
  providerLogo(element('model-logo'), string(data.model));
  element('result-id').textContent = `Result: ${data.job_id}`;
  element('job-status').textContent = stateLabel(data.status);
  element('cost').textContent = costLabel(data.cost_usd);
  element('cleanup').textContent = data.cleanup_pending ? 'Cleanup pending' : '';
  element('prompt-details').hidden = typeof data.prompt !== 'string';
  element('prompt').textContent = string(data.prompt);
  const text = string(data.answer);
  // Keep a user's selection intact when a poll changes only metadata.
  if (renderedAnswer !== text) {
    const wasAtBottom = answer.scrollHeight - answer.scrollTop - answer.clientHeight < 24;
    const wasEmpty = !renderedAnswer;
    const oldTop = answer.scrollTop;
    renderMarkdown(answer, text);
    // Do not pull a reader away from older text as streaming updates arrive.
    answer.scrollTop = !wasEmpty && wasAtBottom ? answer.scrollHeight : oldTop;
    renderedAnswer = text;
  }
  live.render(data, text);
  showError(string(data.error));
  updateButtons();
}

async function callTool(name, jobId) {
  const signal = controller.signal;
  const request = () => {
    signal.throwIfAborted();
    return app.callServerTool({ name, arguments: { job_id: jobId } }, { signal, timeout: 15000 });
  };
  if (name !== 'get_advisor_result') return request();
  // Polling, summary polling and the ownership recheck before sharing share
  // one serial queue, so app-only fetches never overlap.
  const pending = fetchTail.then(request, request);
  fetchTail = pending.then(() => undefined, () => undefined);
  return pending;
}

async function poll(jobId, token) {
  if (disposed || blocked || token !== generation) return;
  try {
    const result = await callTool('get_advisor_result', jobId);
    if (disposed || blocked || token !== generation) return;
    // An app-only fetch is the only source of display text. Never fall back to
    // public content/structuredContent when the UI-only envelope is missing.
    if (result.isError) { suppressResult(toolError(result)); return; }
    const data = result._meta?.advisorResult;
    if (!validResult(data, jobId)) {
      suppressResult('The host returned an invalid advisor result. Reopen this result to try again.');
      return;
    }
    renderOriginal(data);
    if (!terminal.has(data.status)) {
      poller.schedule(jobId, `${data.status}:${data.answer.length}`, () => {
        if (!disposed && !blocked && token === generation) void poll(jobId, token);
      });
    }
  } catch (error) {
    if (disposed || blocked || token !== generation) return;
    suppressResult(`Unable to retrieve this result: ${string(error?.message) || 'Host request failed.'}`);
  }
}

function beginPolling() {
  if (!original || disposed || blocked || !connected) return;
  if (!canCallTools()) {
    showError('This host cannot retrieve the local answer. Open the result in a host that supports app tools.');
    return;
  }
  void poll(original.job_id, generation);
}

function receiveResult(result) {
  if (disposed) return;
  stopPolling();
  if (result.isError) { suppressResult(toolError(result)); return; }
  const data = result.structuredContent;
  if (!validateStatus(data)) { suppressResult('No valid advisor result was supplied by the host.'); return; }
  blocked = false;
  original = null;
  followUpBusy = false;
  share.reset();
  element('prompt-details').open = false;
  // Public tool data identifies the job; even unexpected public answer fields
  // must not be rendered. The first app-only fetch also rechecks ownership.
  const { prompt, answer: ignoredAnswer, ...status } = data;
  renderOriginal(status);
  beginPolling();
}

element('follow-up').addEventListener('input', updateButtons);
element('follow-up-send').addEventListener('click', async () => {
  const draft = element('follow-up').value.trim();
  if (element('follow-up-send').disabled || !draft || !original) return;
  const token = generation;
  const source = { ...original };
  followUpBusy = true;
  element('follow-up-status').textContent = 'Sending to advisor…';
  updateButtons();
  try {
    const result = await app.callServerTool({ name: 'send_advisor_message', arguments: {
      chat_id: source.chat_id, prompt: draft,
    } }, { signal: controller.signal, timeout: 15000 });
    if (disposed || blocked || token !== generation) return;
    if (result.isError) {
      suppressResult(toolError(result));
      element('follow-up-status').textContent = 'Follow-up not confirmed. Draft kept. Reopen the chat before trying again.';
      return;
    }
    const data = result.structuredContent;
    if (!validateStatus(data) || data.job_id === source.job_id || data.chat_id !== source.chat_id
      || data.kind !== 'message' || data.model !== source.model) {
      suppressResult('The host returned an invalid follow-up job. Reopen the chat.');
      element('follow-up-status').textContent = 'Follow-up not confirmed. Draft kept.';
      return;
    }
    element('follow-up').value = '';
    answer.textContent = ''; renderedAnswer = ''; answer.scrollTop = 0;
    receiveResult(result);
    element('follow-up-status').textContent = 'Follow-up sent. The reply appears above.';
  } catch {
    if (disposed || blocked || token !== generation) return;
    // An uncertain submission may already have started inference. Never retry it.
    suppressResult('Unable to confirm the follow-up. Reopen the chat to check its status before retrying.');
    element('follow-up-status').textContent = 'Follow-up not confirmed. Draft kept; check the chat before resending.';
  } finally {
    if (!disposed && token === generation) { followUpBusy = false; updateButtons(); }
  }
});

wireMenu(element('more'));
const share = createShare({
  app, element, poller, callTool, validStatus: validateStatus, validResult, toolError, suppress: suppressResult,
  original: () => original, generation: () => generation, signal: () => controller.signal, onChange: updateButtons,
  current: token => !disposed && !blocked && token === generation,
});

function dispose() {
  disposed = true;
  stopPolling();
  live.stop();
  updateButtons();
}

// Register before connecting: hosts can deliver an initial result immediately.
app.ontoolresult = receiveResult;
app.onhostcontextchanged = setTheme;
app.onteardown = async () => { dispose(); return {}; };
window.addEventListener('pagehide', dispose, { once: true });
app.connect().then(() => {
  if (disposed) return;
  connected = true;
  setTheme(app.getHostContext());
  updateButtons();
  beginPolling();
}).catch(error => {
  if (!disposed) suppressResult(`Unable to connect to the advisor host: ${string(error?.message) || 'Connection failed.'}`);
});
