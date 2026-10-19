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
  initial = { content: [], structuredContent: job() }, responses = {}, capabilities = { serverTools: {}, message: { text: {} } },
  messageResponse = {}, theme = 'light', delay = 0,
} = {}) {
  const requests = [];
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', route => { requests.push(route.request().url()); return route.abort(); });
  await page.setContent('<iframe title="Advisor viewer" sandbox="allow-scripts" style="width:700px;height:700px;border:0"></iframe>');
  await page.evaluate(({ html, initial, responses, capabilities, messageResponse, theme, delay }) => {
    window.hostMessages = [];
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
      if (message.method === 'ui/message') reply(message.id, { result: messageResponse });
      if (message.method === 'tools/call') {
        const key = `${message.params.name}:${message.params.arguments.job_id}`;
        const index = window.toolCounts[key] ?? 0;
        window.toolCounts[key] = index + 1;
        const sequence = responses[key] ?? [];
        const next = sequence[Math.min(index, sequence.length - 1)] ?? { isError: true, content: [{ type: 'text', text: 'Missing test result' }] };
        if (message.params.name === 'get_advisor_result') {
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
  }, { html, initial, responses, capabilities, messageResponse, theme, delay });
  return { viewer: page.frameLocator('iframe'), requests, errors };
}

export async function assertNoHandoff(page) {
  expect(await page.evaluate(() => window.hostMessages.filter(message =>
    message.method === 'ui/message' || message.method === 'ui/update-model-context'))).toEqual([]);
}

export async function selectAnswer(viewer, text) {
  await viewer.locator('#answer').evaluate((element, selectedText) => {
    const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
    let node, start = -1;
    while ((node = walker.nextNode())) {
      start = node.textContent.indexOf(selectedText);
      if (start >= 0) break;
    }
    if (!node) throw new Error('Test selection does not occur in answer');
    const range = document.createRange();
    range.setStart(node, start);
    range.setEnd(node, start + selectedText.length);
    window.getSelection().removeAllRanges();
    window.getSelection().addRange(range);
    document.dispatchEvent(new Event('selectionchange'));
  }, text);
}
