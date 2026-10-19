import { test, expect } from '@playwright/test';
import { job, result, openAdvisor, assertNoHandoff, assertNoChatMessage, contexts, moreAction, selectAnswer } from './fixtures/advisor-host.js';

test.afterEach(async ({ page }) => assertNoChatMessage(page));

const completed = { 'get_advisor_result:result-1': [result()] };

test('views UI-only answer with collapsed prompt and never hands off on view', async ({ page }) => {
  const { viewer, requests, errors } = await openAdvisor(page, { responses: completed });
  await expect(viewer.locator('#answer')).toHaveText(result()._meta.advisorResult.answer);
  await expect(viewer.locator('#prompt-details')).not.toHaveAttribute('open', '');
  await expect(viewer.getByText('example/advisor', { exact: true })).toBeVisible();
  await expect(viewer.locator('#job-status')).toContainText('Completed');
  await expect(viewer.locator('#cost')).toContainText('$0.012');
  await assertNoHandoff(page);
  expect(requests).toEqual([]);
  expect(errors).toEqual([]);
  await page.screenshot({ path: 'test-results/advisor.png' });
});

test('prepares only answer selection and sends the edited preview on explicit click', async ({ page }) => {
  const { viewer } = await openAdvisor(page, { responses: completed });
  await expect(viewer.locator('#answer')).toContainText('Selected insight.');
  await selectAnswer(viewer, 'Selected insight.');
  await viewer.getByRole('button', { name: 'Share selection with Claude', exact: true }).click();
  await expect(viewer.getByLabel('Text to share')).toHaveValue('Selected insight.');
  await assertNoHandoff(page);
  await viewer.getByLabel('Text to share').fill('Edited selected insight.');
  await viewer.getByRole('button', { name: "Add to Claude's context", exact: true }).click();
  await expect(viewer.locator('#handoff-status')).toHaveText("Added. Ask Claude to check the advisor viewer for the answer.");
  const messages = await page.evaluate(() => window.hostMessages.filter(message => message.method === 'ui/update-model-context'));
  expect(messages).toHaveLength(1);
  expect(messages[0].params).toEqual({ content: [{ type: 'text', text: 'Advisor selection from example/advisor (result result-1):\n\nEdited selected insight.' }] });
  expect(messages[0].params.content[0].text).not.toContain('First paragraph.');
});

test('rejects selections outside answer and prepares editable full answer separately', async ({ page }) => {
  const { viewer } = await openAdvisor(page, { responses: completed });
  await expect(viewer.locator('#answer')).toContainText('First paragraph.');
  await viewer.locator('#model').evaluate(element => {
    const range = document.createRange();
    range.selectNodeContents(element);
    window.getSelection().removeAllRanges();
    window.getSelection().addRange(range);
    document.dispatchEvent(new Event('selectionchange'));
  });
  await expect(viewer.getByRole('button', { name: 'Share selection with Claude', exact: true })).toHaveCount(0);
  await viewer.getByRole('button', { name: 'Share with Claude', exact: true }).click();
  await expect(viewer.getByLabel('Text to share')).toHaveValue(result()._meta.advisorResult.answer);
  await assertNoHandoff(page);
});

test('polls partials without overlap and stops once completed', async ({ page }) => {
  const { viewer } = await openAdvisor(page, { initial: { content: [], structuredContent: job({ status: 'queued' }) }, delay: 150,
    responses: { 'get_advisor_result:result-1': [result({ status: 'running', answer: 'Partial answer' }), result()] } });
  await expect(viewer.locator('#answer')).toHaveText('Partial answer');
  await expect(viewer.locator('#job-status')).toContainText('Running');
  await expect(await moreAction(viewer, 'Share a summary')).toBeDisabled();
  await expect(viewer.locator('#answer')).toContainText('Last paragraph.');
  await page.waitForTimeout(1400);
  expect(await page.evaluate(() => window.toolCounts['get_advisor_result:result-1'])).toBe(2);
  expect(await page.evaluate(() => window.maxActiveFetches)).toBe(1);
  await assertNoHandoff(page);
});

test('separate charged summary job retains original and requires review before Send', async ({ page }) => {
  const summary = job({ job_id: 'summary-1', kind: 'summary', source_job_id: 'result-1', status: 'queued' });
  const { viewer } = await openAdvisor(page, { responses: {
    ...completed,
    'summarize_advisor_result:result-1': [{ content: [], structuredContent: summary }],
    'get_advisor_result:summary-1': [result({ ...summary, status: 'running', answer: 'Partial summary' }), result({ ...summary, status: 'completed', answer: 'Short summary', cost_usd: 0.005 })],
    'export_advisor_result:result-1': [{ content: [], structuredContent: { saved: true }, _meta: { advisorExport: { path: 'C:\\exports\\original.md' } } }],
  } });
  await expect(viewer.locator('#answer')).toContainText('Last paragraph.');
  const summarize = await moreAction(viewer, 'Share a summary');
  await expect(viewer.getByText('Summaries use OpenRouter credits.', { exact: true })).toBeVisible();
  await summarize.click();
  await expect(viewer.locator('#summary-status')).toContainText('Running');
  await expect(viewer.locator('#answer')).toHaveText(result()._meta.advisorResult.answer);
  await expect(viewer.getByRole('button', { name: "Add to Claude's context", exact: true })).toBeDisabled();
  await expect(viewer.getByLabel('Text to share')).toHaveValue('Short summary');
  await expect(viewer.locator('#summary-status')).toContainText('$0.005');
  await (await moreAction(viewer, 'Save as file')).click();
  await expect(viewer.locator('#export-status')).toContainText('original.md');
  expect(await page.evaluate(() => window.hostMessages.filter(message => message.method === 'tools/call' && message.params.name === 'export_advisor_result').map(message => message.params.arguments.job_id))).toEqual(['result-1']);
  await assertNoHandoff(page);
  await viewer.getByLabel('Text to share').fill('Reviewed short summary');
  await viewer.getByRole('button', { name: "Add to Claude's context", exact: true }).click();
  await expect(viewer.locator('#handoff-status')).toContainText("Added. Ask Claude to check the advisor viewer for the answer.");
  const messages = await page.evaluate(() => window.hostMessages.filter(message => message.method === 'ui/update-model-context'));
  expect(messages[0].params.content[0].text).toBe('Advisor summary from example/advisor (result result-1; summary summary-1):\n\nReviewed short summary');
});

test('exports original result through app-only tool and displays returned local path', async ({ page }) => {
  const { viewer } = await openAdvisor(page, { responses: { ...completed,
    'export_advisor_result:result-1': [{ content: [], structuredContent: { saved: true }, _meta: { advisorExport: { path: 'C:\\advisor-exports\\result-1.md' } } }],
  } });
  await expect(viewer.locator('#answer')).toContainText('Last paragraph.');
  await (await moreAction(viewer, 'Save as file')).click();
  await expect(viewer.locator('#export-status')).toHaveText('Saved Markdown: C:\\advisor-exports\\result-1.md');
  const calls = await page.evaluate(() => window.hostMessages.filter(message => message.method === 'tools/call' && message.params.name === 'export_advisor_result'));
  expect(calls[0].params.arguments).toEqual({ job_id: 'result-1' });
  await assertNoHandoff(page);
});

test('hostile provider fields remain literal text and make no remote requests', async ({ page }) => {
  const hostile = '<img src="https://evil.test/pixel" onerror="window.pwned=true"><script>alert(1)</script>';
  const { viewer, requests } = await openAdvisor(page, { responses: { 'get_advisor_result:result-1': [result({ answer: hostile, prompt: hostile, title: hostile, model: hostile })] } });
  await expect(viewer.locator('#answer')).toHaveText(hostile);
  await viewer.locator('#prompt-details summary').click();
  await expect(viewer.locator('#prompt')).toHaveText(hostile);
  await expect(viewer.locator('#answer img, #answer script, #answer a, #prompt img')).toHaveCount(0);
  expect(await viewer.locator('body').evaluate(() => window.pwned)).toBeUndefined();
  expect(requests).toEqual([]);
  await assertNoHandoff(page);
});

test('denied polling clears partial result and preview and stops further fetches', async ({ page }) => {
  const { viewer } = await openAdvisor(page, { responses: { 'get_advisor_result:result-1': [result({ status: 'running', answer: 'Private partial' }),
    { isError: true, content: [{ type: 'text', text: 'Access denied: sign-in changed.' }], hold: 'deny' }],
  } });
  await expect(viewer.locator('#answer')).toHaveText('Private partial');
  await viewer.getByRole('button', { name: 'Share with Claude', exact: true }).click();
  await expect(viewer.getByLabel('Text to share')).toHaveValue('Private partial');
  // Deliver the denial only once the preview holds the partial (no race with the 1 s poll).
  await page.evaluate(() => window.release('deny'));
  await expect(viewer.locator('#error')).toContainText('Access denied');
  await expect(viewer.locator('#answer')).toHaveText('');
  await expect(viewer.getByLabel('Text to share')).toHaveValue('');
  await expect(viewer.getByRole('button', { name: "Add to Claude's context", exact: true })).toBeDisabled();
  await page.waitForTimeout(1400);
  expect(await page.evaluate(() => window.toolCounts['get_advisor_result:result-1'])).toBe(2);
  await assertNoHandoff(page);
});

test('interrupted result labels partial text and disables summary', async ({ page }) => {
  const { viewer } = await openAdvisor(page, { responses: { 'get_advisor_result:result-1': [result({ status: 'interrupted', answer: 'Retained partial', error: 'Server restarted', cleanup_pending: true, cost_usd: null })] } });
  await expect(viewer.locator('#answer')).toHaveText('Retained partial');
  await expect(viewer.locator('#job-status')).toContainText('Interrupted');
  await expect(viewer.locator('#answer-state')).toContainText('Partial');
  await expect(viewer.locator('#error')).toContainText('Server restarted');
  await expect(viewer.locator('#cleanup')).toContainText('Cleanup pending');
  await expect(viewer.locator('#cost')).toContainText('unavailable');
  await expect(await moreAction(viewer, 'Share a summary')).toBeDisabled();
});

test('teardown prevents further polling and late rendering', async ({ page }) => {
  const { viewer } = await openAdvisor(page, { responses: { 'get_advisor_result:result-1': [result({ status: 'running', answer: 'Partial' })] } });
  await expect(viewer.locator('#answer')).toHaveText('Partial');
  await page.evaluate(() => document.querySelector('iframe').contentWindow.postMessage({ jsonrpc: '2.0', id: 'teardown', method: 'ui/resource-teardown', params: {} }, '*'));
  await expect.poll(() => page.evaluate(() => window.hostMessages.some(message => message.id === 'teardown' && 'result' in message))).toBe(true);
  const count = await page.evaluate(() => window.toolCounts['get_advisor_result:result-1']);
  await page.waitForTimeout(1400);
  expect(await page.evaluate(() => window.toolCounts['get_advisor_result:result-1'])).toBe(count);
  await assertNoHandoff(page);
});

for (const action of [
  { name: 'Share a summary', tool: 'summarize_advisor_result' },
  { name: 'Save as file', tool: 'export_advisor_result' },
]) {
  test(`denied ${action.tool} suppresses cached answer and handoff`, async ({ page }) => {
    const { viewer } = await openAdvisor(page, { responses: { ...completed,
      [`${action.tool}:result-1`]: [{ isError: true, content: [{ type: 'text', text: 'Access denied: workspace changed.' }] }],
    } });
    await expect(viewer.locator('#answer')).toContainText('First paragraph.');
    await viewer.getByRole('button', { name: 'Share with Claude', exact: true }).click();
    await (await moreAction(viewer, action.name)).click();
    await expect(viewer.locator('#error')).toContainText('Access denied');
    await expect(viewer.locator('#answer')).toHaveText('');
    await expect(viewer.getByLabel('Text to share')).toHaveValue('');
    await expect(viewer.getByRole('button', { name: "Add to Claude's context", exact: true })).toBeDisabled();
    await assertNoHandoff(page);
  });
}

test('failed summary job keeps original, never retries inference and sends nothing', async ({ page }) => {
  const summary = job({ job_id: 'summary-1', kind: 'summary', source_job_id: 'result-1', status: 'queued' });
  const { viewer } = await openAdvisor(page, { responses: { ...completed,
    'summarize_advisor_result:result-1': [{ content: [], structuredContent: summary }],
    'get_advisor_result:summary-1': [result({ ...summary, status: 'failed', answer: 'Unfinished summary', error: 'Provider interrupted the summary.' })],
  } });
  await expect(viewer.locator('#answer')).toContainText('Last paragraph.');
  await (await moreAction(viewer, 'Share a summary')).click();
  await expect(viewer.locator('#summary-error')).toContainText('Provider interrupted');
  await expect(viewer.locator('#answer')).toHaveText(result()._meta.advisorResult.answer);
  await expect(viewer.getByRole('button', { name: "Add to Claude's context", exact: true })).toBeDisabled();
  await page.waitForTimeout(1400);
  expect(await page.evaluate(() => window.toolCounts['summarize_advisor_result:result-1'])).toBe(1);
  expect(await page.evaluate(() => window.toolCounts['get_advisor_result:summary-1'])).toBe(1);
  await assertNoHandoff(page);
});

test('public body fields and invalid UI-only envelope are never rendered', async ({ page }) => {
  const { viewer } = await openAdvisor(page, {
    initial: { content: [{ type: 'text', text: 'Public answer leak' }], structuredContent: { ...job(), answer: 'Public answer leak', prompt: 'Public prompt leak' } },
    responses: { 'get_advisor_result:result-1': [{ content: [{ type: 'text', text: 'Wrong envelope body' }], structuredContent: { ...job(), answer: 'Wrong envelope body' } }] },
  });
  await expect(viewer.locator('#error')).toContainText('invalid advisor result');
  await expect(viewer.locator('#answer')).toHaveText('');
  await expect(viewer.getByText('Public answer leak', { exact: true })).toHaveCount(0);
  await expect(viewer.getByText('Wrong envelope body', { exact: true })).toHaveCount(0);
  await assertNoHandoff(page);
});

test('host without server tools reports limitation and never calls public fallback tools', async ({ page }) => {
  const { viewer } = await openAdvisor(page, { capabilities: { updateModelContext: { text: {} } } });
  await expect(viewer.locator('#error')).toContainText('cannot retrieve the local answer');
  await expect(viewer.getByRole('button', { name: 'Share with Claude', exact: true })).toBeDisabled();
  expect(await page.evaluate(() => window.hostMessages.filter(message => message.method === 'tools/call'))).toEqual([]);
  await assertNoHandoff(page);
});

test('initial waiting state and host themes remain accessible at narrow widths', async ({ page }) => {
  const { viewer } = await openAdvisor(page, { initial: null, theme: 'dark' });
  await expect(viewer.locator('#job-status')).toHaveText('Waiting for an advisor result…');
  await expect(viewer.locator('html')).toHaveAttribute('data-theme', 'dark');
  await page.evaluate(() => { document.querySelector('iframe').style.width = '320px'; window.notify('ui/notifications/host-context-changed', { theme: 'light' }); });
  await expect(viewer.locator('html')).toHaveAttribute('data-theme', 'light');
  expect(await viewer.locator('body').evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  await assertNoHandoff(page);
});

for (const action of [
  { name: "Add to Claude's context", capabilities: { serverTools: {}, updateModelContext: { text: {} } } },
  { name: 'Copy message', capabilities: { serverTools: {} } },
]) {
  for (const denied of [
    { isError: true, content: [{ type: 'text', text: 'Access denied: credential changed.' }] },
    { rpcError: { code: -32000, message: 'Transport verification failed.' } },
  ]) {
    test(`${action.name} rechecks ownership and clears cached content on ${denied.isError ? 'denial' : 'RPC failure'}`, async ({ page }) => {
      const { viewer } = await openAdvisor(page, { capabilities: action.capabilities,
        responses: { 'get_advisor_result:result-1': [result(), denied] },
      });
      await expect(viewer.locator('#answer')).toContainText('First paragraph.');
      await viewer.getByRole('button', { name: 'Share with Claude', exact: true }).click();
      await viewer.getByLabel('Text to share').fill('Private reviewed text');
      await viewer.getByRole('button', { name: action.name, exact: true }).click();
      await expect(viewer.locator('#error')).toContainText(denied.isError ? 'Access denied' : 'Transport verification failed');
      await expect(viewer.locator('#answer')).toHaveText('');
      await expect(viewer.getByLabel('Text to share')).toHaveValue('');
      await expect(viewer.getByLabel('Copyable message')).toHaveValue('');
      await expect(viewer.locator('#send')).toBeDisabled();
      await expect(viewer.locator('#copy')).toBeHidden();
      await assertNoHandoff(page);
    });
  }
}

test('successful handoff revalidation preserves reviewed selection instead of refreshed body', async ({ page }) => {
  const { viewer } = await openAdvisor(page, { responses: { 'get_advisor_result:result-1': [result(), result({ answer: 'Updated owned answer' })] } });
  await expect(viewer.locator('#answer')).toContainText('Selected insight.');
  await selectAnswer(viewer, 'Selected insight.');
  await viewer.getByRole('button', { name: 'Share selection with Claude', exact: true }).click();
  await viewer.getByLabel('Text to share').fill('My reviewed selected insight');
  await viewer.getByRole('button', { name: "Add to Claude's context", exact: true }).click();
  await expect(viewer.locator('#handoff-status')).toHaveText("Added. Ask Claude to check the advisor viewer for the answer.");
  expect(await page.evaluate(() => window.toolCounts['get_advisor_result:result-1'])).toBe(2);
  const messages = await page.evaluate(() => window.hostMessages.filter(message => message.method === 'ui/update-model-context'));
  expect(messages[0].params.content[0].text).toBe('Advisor selection from example/advisor (result result-1):\n\nMy reviewed selected insight');
  await expect(viewer.getByLabel('Text to share')).toHaveValue('My reviewed selected insight');
});

test('summary handoff rechecks original and distinct summary ownership before Send', async ({ page }) => {
  const summary = job({ job_id: 'summary-1', kind: 'summary', source_job_id: 'result-1', status: 'queued' });
  const { viewer } = await openAdvisor(page, { responses: {
    ...completed, 'summarize_advisor_result:result-1': [{ content: [], structuredContent: summary }],
    'get_advisor_result:summary-1': [result({ ...summary, status: 'completed', answer: 'Summary draft' }), { isError: true, content: [{ type: 'text', text: 'Summary access denied.' }] }],
  } });
  await expect(viewer.locator('#answer')).toContainText('Last paragraph.');
  await (await moreAction(viewer, 'Share a summary')).click();
  await expect(viewer.getByLabel('Text to share')).toHaveValue('Summary draft');
  await viewer.getByLabel('Text to share').fill('Reviewed summary');
  await viewer.getByRole('button', { name: "Add to Claude's context", exact: true }).click();
  await expect(viewer.locator('#error')).toContainText('Summary access denied');
  expect(await page.evaluate(() => window.toolCounts['get_advisor_result:result-1'])).toBe(2);
  expect(await page.evaluate(() => window.toolCounts['get_advisor_result:summary-1'])).toBe(2);
  await expect(viewer.getByLabel('Text to share')).toHaveValue('');
  await assertNoHandoff(page);
});
