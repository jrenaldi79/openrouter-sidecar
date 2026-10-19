import { test, expect } from '@playwright/test';
import { openAdvisor, result, job, assertNoHandoff, assertNoChatMessage } from './fixtures/advisor-host.js';

test.afterEach(async ({ page }) => assertNoChatMessage(page));

test('follow-up cannot send while original reply is running', async ({ page }) => {
  const { viewer } = await openAdvisor(page, { responses: {
    'get_advisor_result:result-1': [result({ status: 'running', answer: 'Partial reply' })],
  } });
  await expect(viewer.locator('#answer')).toHaveText('Partial reply');
  await expect(viewer.getByLabel('Message advisor')).toHaveAttribute('readonly', '');
  await expect(viewer.getByRole('button', { name: 'Send follow-up', exact: true })).toBeDisabled();
  await assertNoHandoff(page);
});

test('streaming preserves a reader position above the bottom', async ({ page }) => {
  const text = Array.from({ length: 50 }, (_, i) => `Observation ${i}: Explore this product concept carefully.`).join('\n\n');
  const { viewer } = await openAdvisor(page, { responses: {
    'get_advisor_result:result-1': [result({ status: 'running', answer: text }), result({ answer: text + '\n\nFinal observation.' })],
  } });
  await expect(viewer.locator('#answer')).toContainText('Observation 49');
  await viewer.locator('#answer').evaluate(el => { el.scrollTop = 100; });
  await expect(viewer.locator('#answer')).toContainText('Final observation.');
  expect(await viewer.locator('#answer').evaluate(el => el.scrollTop)).toBe(100);
});

test('long answer scrolls internally without growing with its content', async ({ page }) => {
  const answer = Array.from({ length: 80 }, (_, i) => `Paragraph ${i}: A thoughtful product development observation.`).join('\n\n');
  const { viewer } = await openAdvisor(page, { responses: { 'get_advisor_result:result-1': [result({ answer })] } });
  await expect(viewer.locator('#answer')).toContainText('Paragraph 79');
  for (const width of [360, 900]) {
    await page.evaluate(width => document.querySelector('iframe').style.width = `${width}px`, width);
    const metrics = await viewer.locator('#answer').evaluate(el => ({ height: el.clientHeight, full: el.scrollHeight, overflow: getComputedStyle(el).overflowY }));
    expect(metrics.height).toBeLessThanOrEqual(360);
    expect(metrics.full).toBeGreaterThan(metrics.height);
    expect(metrics.overflow).toBe('auto');
    await viewer.locator('#answer').focus();
    // End scrolls a focused pane on every platform; Control+End is a no-op on macOS.
    await viewer.locator('#answer').press('End');
    expect(await viewer.locator('#answer').evaluate(el => el.scrollTop)).toBeGreaterThan(0);
    await page.locator('iframe').screenshot({ path: `../.venv/codex-install/advisor-follow-up-${width}.png` });
  }
  await assertNoHandoff(page);
});

test('follow-up continues same chat and privately streams the new result', async ({ page }) => {
  const next = job({ job_id: 'result-2', status: 'queued' });
  const { viewer } = await openAdvisor(page, { delay: 150, responses: {
    'get_advisor_result:result-1': [result()],
    'send_advisor_message:undefined': [{ content: [], structuredContent: next }],
    'get_advisor_result:result-2': [result({ ...next, status: 'running', prompt: 'What should I test first?', answer: 'Start with' }), result({ ...next, status: 'completed', prompt: 'What should I test first?', answer: 'Start with user interviews.' })],
  } });
  await expect(viewer.locator('#answer')).toContainText('Last paragraph.');
  await viewer.getByLabel('Message advisor').fill('What should I test first?');
  await viewer.getByRole('button', { name: 'Send follow-up', exact: true }).click();
  await expect(viewer.getByRole('button', { name: 'Send follow-up', exact: true })).toBeDisabled();
  await expect(viewer.getByRole('button', { name: 'Share with Claude', exact: true })).toBeDisabled();
  await expect(viewer.locator('#answer')).toHaveText('Start with user interviews.');
  await expect(viewer.getByLabel('Message advisor')).toHaveValue('');
  const sends = await page.evaluate(() => window.hostMessages.filter(m => m.method === 'tools/call' && m.params.name === 'send_advisor_message'));
  expect(sends).toHaveLength(1);
  expect(sends[0].params.arguments).toEqual({ chat_id: 'chat-1', prompt: 'What should I test first?' });
  await assertNoHandoff(page);
});

for (const [label, response] of [
  ['tool failure', { isError: true, content: [{ type: 'text', text: 'Access denied' }] }],
  ['wrong chat', { content: [], structuredContent: job({ job_id: 'result-2', chat_id: 'other-chat' }) }],
  ['transport failure', { rpcError: { code: -32000, message: 'Offline' } }],
]) {
  test(`follow-up ${label} retains draft and never sends parent context`, async ({ page }) => {
    const { viewer } = await openAdvisor(page, { responses: {
      'get_advisor_result:result-1': [result()], 'send_advisor_message:undefined': [response],
    } });
    await expect(viewer.locator('#answer')).toContainText('Last paragraph.');
    await viewer.getByLabel('Message advisor').fill('Keep this draft');
    await viewer.getByRole('button', { name: 'Send follow-up', exact: true }).click();
    await expect(viewer.locator('#follow-up-status')).toContainText('not confirmed');
    await expect(viewer.getByLabel('Message advisor')).toHaveValue('Keep this draft');
    await assertNoHandoff(page);
    expect(await page.evaluate(() => window.toolCounts['send_advisor_message:undefined'])).toBe(1);
  });
}
