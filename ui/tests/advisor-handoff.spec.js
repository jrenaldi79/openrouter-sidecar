import { test, expect } from '@playwright/test';
import {
  result, summaryResult, openAdvisor, assertNoHandoff, assertNoChatMessage, contexts, moreAction,
  longAnswer, longWithSummary, FULL_ANSWER_LIMIT, SHARED_SUMMARY, SHARED_FULL, shareButton, toolCount,
} from './fixtures/advisor-host.js';

const completed = { 'get_advisor_result:result-1': [result()] };
const answer = result()._meta.advisorResult.answer;
const copyOnly = { serverTools: {} };
const framed = body => `Advisor answer from example/advisor (result result-1):\n\n${body}`;
const framedSummary = body => `Advisor summary from example/advisor (result result-1; summary summary-1):\n\n${body}`;
const fakeClipboard = viewer => viewer.locator('body').evaluate(() => Object.defineProperty(navigator, 'clipboard', {
  configurable: true, value: { writeText: async text => { window.copied = text; } },
}));
const copied = viewer => viewer.locator('body').evaluate(() => window.copied);

test.afterEach(async ({ page }) => assertNoChatMessage(page));

test('one click summarizes a long answer, shows progress and adds only the summary', async ({ page }) => {
  const { viewer } = await openAdvisor(page, { responses: longWithSummary([
    { ...summaryResult({ status: 'running', answer: 'Partial summary' }), hold: 'summary' }, summaryResult(),
  ]) });
  await expect(viewer.locator('#answer')).toHaveText(longAnswer);
  await shareButton(viewer).click();
  await expect(viewer.locator('#handoff-status')).toHaveText('Summarizing… (uses OpenRouter credits)');
  await expect(shareButton(viewer)).toBeDisabled();
  await expect(await moreAction(viewer, 'Share full answer')).toBeDisabled();
  await assertNoHandoff(page);
  await page.evaluate(() => window.release('summary'));
  await expect(viewer.locator('#handoff-status')).toHaveText(SHARED_SUMMARY);
  const sent = await contexts(page);
  expect(sent).toHaveLength(1);
  expect(sent[0].params).toEqual({ content: [{ type: 'text', text: framedSummary('Short summary') }] });
  expect(await toolCount(page, 'summarize_advisor_result:result-1')).toBe(1);
  // The original and the summary are both rechecked right before sharing.
  expect(await toolCount(page, 'get_advisor_result:result-1')).toBe(2);
  expect(await toolCount(page, 'get_advisor_result:summary-1')).toBe(3);
  await expect(viewer.locator('#answer')).toHaveText(longAnswer);
});

for (const [label, body] of [['short', answer], [`${FULL_ANSWER_LIMIT}-character`, 'S'.repeat(FULL_ANSWER_LIMIT)]]) {
  test(`a ${label} answer is shared whole in one click without a paid summary`, async ({ page }) => {
    const { viewer } = await openAdvisor(page, { responses: { 'get_advisor_result:result-1': [result({ answer: body })] } });
    await expect(viewer.locator('#answer')).toHaveText(body);
    await shareButton(viewer).click();
    await expect(viewer.locator('#handoff-status')).toHaveText(SHARED_FULL);
    expect((await contexts(page))[0].params.content[0].text).toBe(framed(body));
    expect(await toolCount(page, 'summarize_advisor_result:result-1')).toBe(0);
  });
}

test('there is no editable preview panel', async ({ page }) => {
  const { viewer } = await openAdvisor(page, { responses: completed });
  await expect(viewer.locator('#answer')).toContainText('Last paragraph.');
  await shareButton(viewer).click();
  await expect(viewer.locator('#handoff-status')).toHaveText(SHARED_FULL);
  await expect(viewer.locator('#preview-section, #preview, #send, #cancel-share')).toHaveCount(0);
  await expect(viewer.getByLabel('Text to share')).toHaveCount(0);
  await expect(viewer.getByRole('button', { name: /Add to Claude|Cancel/ })).toHaveCount(0);
  await expect(viewer.locator('textarea:not([readonly])')).toHaveCount(1);
});

test('More → Share full answer shares a long answer whole, with no summary', async ({ page }) => {
  const { viewer } = await openAdvisor(page, { responses: longWithSummary() });
  await expect(viewer.locator('#answer')).toHaveText(longAnswer);
  await (await moreAction(viewer, 'Share full answer')).click();
  await expect(viewer.locator('#handoff-status')).toHaveText(SHARED_FULL);
  expect((await contexts(page))[0].params.content[0].text).toBe(framed(longAnswer));
  expect(await toolCount(page, 'summarize_advisor_result:result-1')).toBe(0);
  expect(await viewer.locator('#more').evaluate(element => element.open)).toBe(false);
});

test('the caption says a summary uses OpenRouter credits', async ({ page }) => {
  const { viewer } = await openAdvisor(page, { responses: completed });
  await expect(viewer.locator('#share-note')).toHaveText('Long answers are shared as a summary, which uses OpenRouter credits.');
  await expect(viewer.locator('#share-note')).toBeVisible();
});

const shares = page => page.evaluate(() => window.shares ?? []);
const shareFails = { 'share_advisor_result:result-1': [{ isError: true, content: [{ type: 'text', text: 'Share failed.' }] }] };

test('hosts without context updates still save the summary for Claude to read', async ({ page }) => {
  const { viewer } = await openAdvisor(page, { capabilities: copyOnly, responses: longWithSummary() });
  await expect(viewer.locator('#answer')).toHaveText(longAnswer);
  await shareButton(viewer).click();
  await expect(viewer.locator('#handoff-status')).toHaveText(SHARED_SUMMARY);
  expect(await shares(page)).toEqual([{ job_id: 'result-1', summary_job_id: 'summary-1' }]);
  await assertNoHandoff(page);
});

test('a failed save says nothing was shared and copies instead', async ({ page }) => {
  const { viewer } = await openAdvisor(page, { responses: { ...completed, ...shareFails } });
  await expect(viewer.locator('#answer')).toContainText('Last paragraph.');
  await fakeClipboard(viewer);
  await shareButton(viewer).click();
  await expect(viewer.locator('#handoff-status')).toHaveText(
    'Share failed. Nothing was shared with Claude. Copied the full answer. Paste it into the chat.');
  expect(await copied(viewer)).toBe(framed(answer));
  await assertNoHandoff(page);
});

test('a blocked clipboard shows the text read-only for manual copy', async ({ page }) => {
  const { viewer } = await openAdvisor(page, { capabilities: copyOnly, responses: { ...completed, ...shareFails } });
  await expect(viewer.locator('#answer')).toContainText('First paragraph.');
  await viewer.locator('body').evaluate(() => Object.defineProperty(navigator, 'clipboard', { configurable: true, value: undefined }));
  await shareButton(viewer).click();
  await expect(viewer.locator('#handoff-status')).toContainText('Clipboard access is unavailable');
  await expect(viewer.getByLabel('Copyable message')).toBeVisible();
  await expect(viewer.getByLabel('Copyable message')).toHaveAttribute('readonly', '');
  await expect(viewer.getByLabel('Copyable message')).toHaveValue(framed(answer));
  await assertNoHandoff(page);
});

test('a rejected context update still counts as shared: the saved share is what Claude reads', async ({ page }) => {
  const { viewer } = await openAdvisor(page, { responses: completed,
    contextResponse: { rpcError: { code: -32000, message: 'Context rejected.' } } });
  await expect(viewer.locator('#answer')).toContainText('Last paragraph.');
  await shareButton(viewer).click();
  await expect(viewer.locator('#handoff-status')).toHaveText(SHARED_FULL);
  expect(await shares(page)).toEqual([{ job_id: 'result-1' }]);
  expect(await contexts(page)).toHaveLength(1);
});

test('More → Copy answer copies the framed full answer', async ({ page }) => {
  const { viewer } = await openAdvisor(page, { responses: completed });
  await expect(viewer.locator('#answer')).toContainText('Last paragraph.');
  await fakeClipboard(viewer);
  await (await moreAction(viewer, 'Copy answer')).click();
  await expect(viewer.locator('#handoff-status')).toHaveText('Copied the full answer. Paste it into the chat.');
  expect(await copied(viewer)).toBe(framed(answer));
  expect(await toolCount(page, 'get_advisor_result:result-1')).toBe(2);
  await assertNoHandoff(page);
});

for (const [label, summaries, summarize] of [
  ['failed summary job', [summaryResult({ status: 'failed', answer: '', error: 'Provider interrupted the summary.' })], null],
  ['summary request failure', null, { rpcError: { code: -32000, message: 'Provider interrupted the summary.' } }],
]) {
  test(`${label} shows the error and offers the full answer instead`, async ({ page }) => {
    const responses = longWithSummary(summaries ?? undefined);
    if (summarize) responses['summarize_advisor_result:result-1'] = [summarize];
    const { viewer } = await openAdvisor(page, { responses });
    await expect(viewer.locator('#answer')).toHaveText(longAnswer);
    await shareButton(viewer).click();
    await expect(viewer.locator('#share-error')).toContainText('Provider interrupted the summary.');
    await expect(viewer.locator('#share-error')).toContainText('Nothing was shared.');
    await assertNoHandoff(page);
    await viewer.getByRole('button', { name: 'Share full answer instead', exact: true }).click();
    await expect(viewer.locator('#handoff-status')).toHaveText(SHARED_FULL);
    await expect(viewer.locator('#share-error')).toBeHidden();
    expect((await contexts(page))[0].params.content[0].text).toBe(framed(longAnswer));
    expect(await toolCount(page, 'summarize_advisor_result:result-1')).toBe(1);
  });
}

test('secondary actions stay tucked in a More menu that closes after a choice', async ({ page }) => {
  const { viewer } = await openAdvisor(page, { responses: completed });
  await expect(viewer.locator('#answer')).toContainText('Last paragraph.');
  for (const name of ['Share full answer', 'Copy answer']) {
    await expect(viewer.getByRole('button', { name, exact: true })).toBeHidden();
  }
  await fakeClipboard(viewer);
  const copy = await moreAction(viewer, 'Copy answer');
  await expect(viewer.locator('#more-menu button')).toHaveText(['Share full answer', 'Copy answer']);
  await expect(viewer.getByRole('button', { name: /summary|selection/i })).toHaveCount(0);
  await copy.click();
  await expect(viewer.locator('#handoff-status')).toHaveText('Copied the full answer. Paste it into the chat.');
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
  await expect(shareButton(viewer)).toBeEnabled();
  expect(await style('#share', 'backgroundColor')).toBe(purple);
  expect(await style('#share', 'color')).toBe('rgb(255, 255, 255)');
  expect(await style('#follow-up-send', 'backgroundColor')).not.toBe(purple);
});
