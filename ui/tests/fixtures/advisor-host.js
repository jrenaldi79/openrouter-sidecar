import { expect } from '@playwright/test';
import { readFile } from 'node:fs/promises';

const html = await readFile(new URL('../../../src/openrouter_image_mcp/ui/advisor.html', import.meta.url), 'utf8')
  .catch(error => { if (error.code === 'ENOENT') return '<!doctype html><body></body>'; throw error; });

export const job = (overrides = {}) => ({
  job_id: 'result-1', chat_id: 'chat-1', model: 'example/advisor', title: 'Study advice',
  status: 'completed', cost_usd: 0.012, cleanup_pending: false, error: null,
  kind: 'message', source_job_id: null, ...overrides,
});
export const result = (overrides = {}) => {
  const data = { ...job(), prompt: 'Explain this topic.', answer: 'First paragraph.\n\nSelected insight.\n\nLast paragraph.', created_at: '2026-10-06T12:00:00Z', ...overrides };
  const { prompt, answer, created_at, ...status } = data;
  return { content: [], structuredContent: status, _meta: { advisorResult: data } };
};

export async function openAdvisor(page, {
  initial = { content: [], structuredContent: job() }, responses = {}, capabilities = { serverTools: {}, updateModelContext: { text: {} } },
  contextResponse = {}, theme = 'light', delay = 0,
} = {}) {
  const requests = [];
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', route => { requests.push(route.request().url()); return route.abort(); });
  await page.setContent('<iframe title="Advisor viewer" sandbox="allow-scripts" style="width:700px;height:700px;border:0"></iframe>');
  await page.evaluate(({ html, initial, responses, capabilities, contextResponse, theme, delay }) => {
    window.hostMessages = [];
    window.pollTimes = [];
    window.toolCounts = {};
    window.activeFetches = 0;
    window.maxActiveFetches = 0;
    window.released = new Set();
    window.holds = [];
    window.release = name => {
      window.released.add(name);
      const waiting = window.holds;
      window.holds = [];
      for (const [held, send] of waiting) {
        if (held === name) send();
        else window.holds.push([held, send]);
      }
    };
    const iframe = document.querySelector('iframe');
    const reply = (id, value) => iframe.contentWindow.postMessage({ jsonrpc: '2.0', id, ...value }, '*');
    window.notify = (method, params) => iframe.contentWindow.postMessage({ jsonrpc: '2.0', method, params }, '*');
    window.addEventListener('message', event => {
      if (event.source !== iframe.contentWindow) return;
      const message = event.data;
      window.hostMessages.push(message);
      if (message.method === 'ui/initialize') {
        reply(message.id, { result: {
          protocolVersion: '2026-01-26', hostInfo: { name: 'Offline advisor host', version: '1' },
          hostCapabilities: capabilities, hostContext: { theme, displayMode: 'inline' },
        } });
        if (initial) window.notify('ui/notifications/tool-result', initial);
      }
      if (message.method === 'ui/notifications/size-changed') iframe.style.height = `${message.params.height}px`;
      if (message.method === 'ui/message') reply(message.id, { result: {} });
      if (message.method === 'ui/update-model-context') {
        reply(message.id, contextResponse.rpcError ? { error: contextResponse.rpcError } : { result: contextResponse });
      }
      if (message.method === 'tools/call') {
        const key = `${message.params.name}:${message.params.arguments.job_id}`;
        const index = window.toolCounts[key] ?? 0;
        window.toolCounts[key] = index + 1;
        const sequence = responses[key] ?? [];
        const next = sequence[Math.min(index, sequence.length - 1)] ?? { isError: true, content: [{ type: 'text', text: 'Missing test result' }] };
        if (message.params.name === 'get_advisor_result') {
          window.pollTimes.push(performance.now());
          window.activeFetches++;
          window.maxActiveFetches = Math.max(window.maxActiveFetches, window.activeFetches);
        }
        // A response with `hold: name` waits until the test calls window.release(name).
        const { hold, ...value } = next;
        const send = () => {
          if (message.params.name === 'get_advisor_result') window.activeFetches--;
          reply(message.id, value.rpcError ? { error: value.rpcError } : { result: value });
        };
        setTimeout(() => (hold && !window.released.has(hold) ? window.holds.push([hold, send]) : send()), delay);
      }
    });
    iframe.srcdoc = html;
  }, { html, initial, responses, capabilities, contextResponse, theme, delay });
  return { viewer: page.frameLocator('iframe'), requests, errors };
}

// Secondary actions sit in the "More" disclosure; open it before using one.
export async function moreAction(viewer, name) {
  const menu = viewer.locator('#more');
  if (!await menu.evaluate(element => element.open)) await viewer.getByText('More', { exact: true }).click();
  return viewer.getByRole('button', { name, exact: true });
}

// A long answer (over FULL_ANSWER_LIMIT in ui/src/advisor-share.js) and its paid summary job.
export const FULL_ANSWER_LIMIT = 3000;
export const longAnswer = 'L'.repeat(FULL_ANSWER_LIMIT + 1);
export const summaryJob = job({ job_id: 'summary-1', kind: 'summary', source_job_id: 'result-1', status: 'queued' });
export const summaryResult = (overrides = {}) => result({ ...summaryJob, status: 'completed', answer: 'Short summary', cost_usd: 0.005, ...overrides });
export const longWithSummary = (summaries = [summaryResult()]) => ({
  'get_advisor_result:result-1': [result({ answer: longAnswer })],
  'summarize_advisor_result:result-1': [{ content: [], structuredContent: summaryJob }],
  'get_advisor_result:summary-1': summaries,
});

export const SHARED_SUMMARY = 'Shared a summary. Ask Claude to check the advisor viewer for it.';
export const SHARED_FULL = 'Shared the full answer. Ask Claude to check the advisor viewer for it.';
export const shareButton = viewer => viewer.getByRole('button', { name: 'Share with Claude', exact: true });
export const toolCount = (page, key) => page.evaluate(name => window.toolCounts[name] ?? 0, key);

export const contexts = page => page.evaluate(() => window.hostMessages.filter(message =>
  message.method === 'ui/update-model-context'));

// The viewer never posts into the chat as the user (that triggers a host prompt).
export async function assertNoChatMessage(page) {
  expect(await page.evaluate(() => (window.hostMessages ?? []).filter(message => message.method === 'ui/message'))).toEqual([]);
}

export async function assertNoHandoff(page) {
  expect(await page.evaluate(() => window.hostMessages.filter(message =>
    message.method === 'ui/message' || message.method === 'ui/update-model-context'))).toEqual([]);
}
