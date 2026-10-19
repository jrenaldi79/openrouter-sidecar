import { test, expect } from '@playwright/test';
import { result, openAdvisor, assertNoHandoff, assertNoChatMessage, contexts, moreAction, selectAnswer } from './fixtures/advisor-host.js';

const completed = { 'get_advisor_result:result-1': [result()] };
const answer = result()._meta.advisorResult.answer;
const copyOnly = { serverTools: {} };
const fakeClipboard = viewer => viewer.locator('body').evaluate(() => Object.defineProperty(navigator, 'clipboard', {
  configurable: true, value: { writeText: async text => { window.copied = text; } },
}));

test.afterEach(async ({ page }) => assertNoChatMessage(page));

test('adds the reviewed answer to Claude\'s context on click', async ({ page }) => {
  const { viewer } = await openAdvisor(page, { responses: completed });
  await expect(viewer.locator('#answer')).toContainText('Last paragraph.');
  await viewer.getByRole('button', { name: 'Share with Claude', exact: true }).click();
  await expect(viewer.getByLabel('Text to share')).toHaveValue(answer);
  await expect(viewer.getByRole('button', { name: 'Copy message', exact: true })).toBeHidden();
  await assertNoHandoff(page);
  await viewer.getByLabel('Text to share').fill('Edited answer for Claude');
  await viewer.getByRole('button', { name: 'Add to Claude\'s context', exact: true }).click();
  await expect(viewer.locator('#handoff-status')).toHaveText('Added. Ask Claude to use the advisor\'s answer.');
  const sent = await contexts(page);
  expect(sent).toHaveLength(1);
  expect(sent[0].params).toEqual({ content: [{ type: 'text',
    text: 'Advisor answer from example/advisor (result result-1):\n\nEdited answer for Claude' }] });
});

test('a rejected context update says nothing was added and falls back to copy', async ({ page }) => {
  const { viewer } = await openAdvisor(page, { responses: completed,
    contextResponse: { rpcError: { code: -32000, message: 'Context rejected.' } } });
  await expect(viewer.locator('#answer')).toContainText('Last paragraph.');
  await fakeClipboard(viewer);
  await viewer.getByRole('button', { name: 'Share with Claude', exact: true }).click();
  await viewer.getByRole('button', { name: 'Add to Claude\'s context', exact: true }).click();
  await expect(viewer.locator('#handoff-status')).toContainText('Nothing was added');
  await viewer.getByRole('button', { name: 'Copy message', exact: true }).click();
  await expect(viewer.locator('#handoff-status')).toHaveText('Copied. Paste it into the chat.');
  expect(await viewer.locator('body').evaluate(() => window.copied)).toBe(`Advisor answer from example/advisor (result result-1):\n\n${answer}`);
  expect(await contexts(page)).toHaveLength(1);
});

test('hosts without context updates offer copy only, with the same framed text', async ({ page }) => {
  const { viewer } = await openAdvisor(page, { capabilities: copyOnly, responses: completed });
  await expect(viewer.locator('#answer')).toContainText('Last paragraph.');
  await fakeClipboard(viewer);
  await viewer.getByRole('button', { name: 'Share with Claude', exact: true }).click();
  await expect(viewer.getByRole('button', { name: 'Add to Claude\'s context', exact: true })).toBeHidden();
  await assertNoHandoff(page);
  await viewer.getByRole('button', { name: 'Copy message', exact: true }).click();
  await expect(viewer.locator('#handoff-status')).toHaveText('Copied. Paste it into the chat.');
  await expect(viewer.getByLabel('Copyable message')).toHaveValue(`Advisor answer from example/advisor (result result-1):\n\n${answer}`);
  expect(await viewer.locator('body').evaluate(() => window.copied)).toBe(`Advisor answer from example/advisor (result result-1):\n\n${answer}`);
  await assertNoHandoff(page);
});

test('host without context updates offers copy, with a manual copy box when the clipboard is blocked', async ({ page }) => {
  const { viewer } = await openAdvisor(page, { capabilities: { serverTools: {} }, responses: completed });
  await expect(viewer.locator('#answer')).toContainText('First paragraph.');
  await viewer.getByRole('button', { name: 'Share with Claude', exact: true }).click();
  await expect(viewer.getByRole('button', { name: "Add to Claude's context", exact: true })).toBeHidden();
  await expect(viewer.getByRole('button', { name: 'Copy message' })).toBeVisible();
  await expect(viewer.locator('#preview-context')).toContainText('cannot add context');
  await viewer.locator('body').evaluate(() => Object.defineProperty(navigator, 'clipboard', { configurable: true, value: undefined }));
  await viewer.getByRole('button', { name: 'Copy message' }).click();
  await expect(viewer.locator('#handoff-status')).toContainText('Clipboard access is unavailable');
  await expect(viewer.getByLabel('Copyable message')).toBeVisible();
  await expect(viewer.getByLabel('Copyable message')).toHaveValue(/^Advisor answer from example\/advisor \(result result-1\):/);
  await assertNoHandoff(page);
});

test('the share button uses selected answer text when there is a selection', async ({ page }) => {
  const { viewer } = await openAdvisor(page, { responses: completed });
  await expect(viewer.locator('#answer')).toContainText('Selected insight.');
  await selectAnswer(viewer, 'Selected insight.');
  await viewer.getByRole('button', { name: 'Share selection with Claude', exact: true }).click();
  await expect(viewer.getByLabel('Text to share')).toHaveValue('Selected insight.');
  await viewer.getByRole('button', { name: 'Add to Claude\'s context', exact: true }).click();
  await expect(viewer.locator('#handoff-status')).toHaveText('Added. Ask Claude to use the advisor\'s answer.');
  expect((await contexts(page))[0].params.content[0].text).toBe('Advisor selection from example/advisor (result result-1):\n\nSelected insight.');
});

test('cancel closes the review without sharing anything', async ({ page }) => {
  const { viewer } = await openAdvisor(page, { responses: completed });
  await expect(viewer.locator('#answer')).toContainText('Last paragraph.');
  await viewer.getByRole('button', { name: 'Share with Claude', exact: true }).click();
  await expect(viewer.locator('#preview-section')).toBeVisible();
  await viewer.getByRole('button', { name: 'Cancel', exact: true }).click();
  await expect(viewer.locator('#preview-section')).toBeHidden();
  await page.waitForTimeout(300);
  await assertNoHandoff(page);
});

test('secondary actions stay tucked in a More menu that closes after a choice', async ({ page }) => {
  const { viewer } = await openAdvisor(page, { responses: { ...completed,
    'export_advisor_result:result-1': [{ content: [], structuredContent: { saved: true }, _meta: { advisorExport: { path: '/tmp/result-1.md' } } }],
  } });
  await expect(viewer.locator('#answer')).toContainText('Last paragraph.');
  await expect(viewer.getByRole('button', { name: 'Save as file', exact: true })).toBeHidden();
  await expect(viewer.getByRole('button', { name: 'Share a summary', exact: true })).toBeHidden();
  const save = await moreAction(viewer, 'Save as file');
  await expect(save).toBeVisible();
  await expect(viewer.locator('#more-menu')).toContainText('Summaries use OpenRouter credits.');
  await save.click();
  await expect(viewer.locator('#export-status')).toContainText('/tmp/result-1.md');
  expect(await viewer.locator('#more').evaluate(element => element.open)).toBe(false);
  await assertNoHandoff(page);
});

test('the viewer has no settings button and never posts anything by itself', async ({ page }) => {
  const { viewer } = await openAdvisor(page, { responses: completed });
  await expect(viewer.locator('#answer')).toContainText('Last paragraph.');
  await expect(viewer.getByRole('button', { name: /settings/i })).toHaveCount(0);
  await viewer.getByText('More', { exact: true }).click();
  await expect(viewer.getByRole('button', { name: /settings/i })).toHaveCount(0);
  await page.waitForTimeout(1200);
  await assertNoHandoff(page);
});

test('Share with Claude is the filled call to action; follow-up send is secondary', async ({ page }) => {
  const { viewer } = await openAdvisor(page, { responses: completed });
  await expect(viewer.locator('#answer')).toContainText('Last paragraph.');
  const purple = 'rgb(78, 42, 132)';
  const style = (selector, prop) => viewer.locator(selector).evaluate((el, p) => getComputedStyle(el)[p], prop);
  const share = viewer.getByRole('button', { name: 'Share with Claude', exact: true });
  await expect(share).toBeEnabled();
  expect(await style('#prepare-full', 'backgroundColor')).toBe(purple);
  expect(await style('#prepare-full', 'color')).toBe('rgb(255, 255, 255)');
  expect(await style('#follow-up-send', 'backgroundColor')).not.toBe(purple);
  await share.click();
  expect(await style('#send', 'backgroundColor')).toBe(purple);
});
