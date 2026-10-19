import { test, expect } from '@playwright/test';
import { job, result, openAdvisor, assertNoHandoff, assertNoChatMessage } from './fixtures/advisor-host.js';
import { elapsedLabel, nextPollDelay, POLL_BASE_MS, POLL_MAX_MS } from '../src/advisor-progress.js';

test.afterEach(async ({ page }) => assertNoChatMessage(page));

const ago = seconds => new Date(Date.now() - seconds * 1000).toISOString();
const running = (answer, extra = {}) => result({ status: 'running', answer, cost_usd: null, created_at: ago(65), ...extra });
const queued = { content: [], structuredContent: job({ status: 'queued', cost_usd: null }) };
const polls = page => page.evaluate(() => window.toolCounts['get_advisor_result:result-1'] ?? 0);

test('elapsed labels and poll backoff are pure and bounded', () => {
  expect(elapsedLabel(-500)).toBe('0s');
  expect(elapsedLabel(9_900)).toBe('9s');
  expect(elapsedLabel(65_000)).toBe('1m 05s');
  expect(nextPollDelay(undefined, true)).toBe(POLL_BASE_MS);
  expect(nextPollDelay(POLL_BASE_MS, false)).toBe(1500);
  expect(nextPollDelay(1500, true)).toBe(POLL_BASE_MS);
  let delay = POLL_BASE_MS;
  for (let i = 0; i < 10; i++) delay = nextPollDelay(delay, false);
  expect(delay).toBe(POLL_MAX_MS);
});

test('queued and thinking jobs show a calm live state with elapsed time', async ({ page }) => {
  const { viewer } = await openAdvisor(page, { initial: queued, responses: {
    'get_advisor_result:result-1': [{ ...running(''), hold: 'first' }, { ...running(''), hold: 'never' }],
  } });
  await expect(viewer.locator('#live-state')).toBeVisible();
  await expect(viewer.locator('#live-label')).toHaveText('Queued, waiting to start…');
  await page.evaluate(() => window.release('first'));
  await expect(viewer.locator('#live-label')).toHaveText('Advisor is thinking…');
  // Elapsed time counts from the server's created_at, not from when the viewer opened.
  await expect(viewer.locator('#live-elapsed')).toHaveText(/^1m 0[5-9]s$/);
  await expect(viewer.locator('#answer-skeleton')).toBeVisible();
  await expect(viewer.locator('#answer')).toHaveAttribute('aria-busy', 'true');
  const first = await viewer.locator('#live-elapsed').textContent();
  await expect(viewer.locator('#live-elapsed')).not.toHaveText(first);
  await assertNoHandoff(page);
});

test('partial text streams in and grows across polls, then completion replaces the live state', async ({ page }) => {
  const { viewer } = await openAdvisor(page, { initial: queued, responses: { 'get_advisor_result:result-1': [
    running('First words'), running('First words and more'), { ...running('First words and more, then the rest'), hold: 'third' },
    { ...result({ answer: 'First words and more, then the rest. Done.' }), hold: 'finish' },
  ] } });
  await expect(viewer.locator('#answer')).toHaveText('First words');
  await expect(viewer.locator('#live-label')).toHaveText('Generating answer…');
  await expect(viewer.locator('#answer-skeleton')).toBeHidden();
  await expect(viewer.locator('#answer')).toHaveText('First words and more');
  await page.evaluate(() => window.release('third'));
  await expect(viewer.locator('#answer')).toHaveText('First words and more, then the rest');
  await expect(viewer.locator('#live-state')).toBeVisible();
  await page.evaluate(() => window.release('finish'));
  await expect(viewer.locator('#answer')).toHaveText('First words and more, then the rest. Done.');
  await expect(viewer.locator('#live-state')).toBeHidden();
  await expect(viewer.locator('#answer-skeleton')).toBeHidden();
  await expect(viewer.locator('#answer')).toHaveAttribute('aria-busy', 'false');
  await expect(viewer.locator('#job-status')).toHaveText('Completed');
  const count = await polls(page);
  await page.waitForTimeout(1600);
  expect(await polls(page)).toBe(count);
  expect(await page.evaluate(() => window.maxActiveFetches)).toBe(1);
});

for (const outcome of [
  { status: 'failed', error: 'Advisor did not complete; no automatic retry was made.' },
  { status: 'interrupted', error: 'Advisor work was interrupted; no automatic retry was made.' },
]) {
  test(`${outcome.status} job shows its error, keeps partial text labelled and stops polling`, async ({ page }) => {
    const { viewer } = await openAdvisor(page, { initial: queued, responses: { 'get_advisor_result:result-1': [
      running('Half an answer'), result({ ...outcome, answer: 'Half an answer', cost_usd: null }),
    ] } });
    await expect(viewer.locator('#live-label')).toHaveText('Generating answer…');
    await expect(viewer.locator('#error')).toHaveText(outcome.error);
    await expect(viewer.locator('#live-state')).toBeHidden();
    await expect(viewer.locator('#answer')).toHaveText('Half an answer');
    await expect(viewer.locator('#answer-state')).toHaveText('Partial answer. This result did not complete.');
    await page.waitForTimeout(1600);
    expect(await polls(page)).toBe(2);
  });
}

test('failed job without text says no answer arrived', async ({ page }) => {
  const { viewer } = await openAdvisor(page, { responses: { 'get_advisor_result:result-1': [
    result({ status: 'failed', answer: '', error: 'Provider failed.', cost_usd: null }),
  ] } });
  await expect(viewer.locator('#error')).toHaveText('Provider failed.');
  await expect(viewer.locator('#answer-state')).toHaveText('No answer text arrived.');
  await expect(viewer.locator('#answer-skeleton')).toBeHidden();
});

test('polling backs off while the answer is unchanged and speeds up when text grows', async ({ page }) => {
  test.setTimeout(20000);
  await openAdvisor(page, { initial: queued, responses: { 'get_advisor_result:result-1': [
    running(''), running(''), running(''), running(''), running('Now writing'), running('Now writing more'), { ...running('x'), hold: 'never' },
  ] } });
  await expect.poll(() => page.evaluate(() => window.pollTimes.length), { timeout: 12000 }).toBeGreaterThanOrEqual(7);
  const times = await page.evaluate(() => window.pollTimes);
  const gaps = times.slice(1).map((time, i) => time - times[i]);
  // Unchanged: 1000, 1500, 2250 ms. Changed (text appears, then grows): back to 1000 ms.
  expect(gaps[0]).toBeGreaterThan(900);
  expect(gaps[0]).toBeLessThan(1400);
  expect(gaps[2]).toBeGreaterThan(2000);
  expect(gaps[4]).toBeLessThan(1400);
  expect(gaps[5]).toBeLessThan(1400);
  expect(await page.evaluate(() => window.maxActiveFetches)).toBe(1);
});
