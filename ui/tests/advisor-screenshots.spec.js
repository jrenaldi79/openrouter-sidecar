import { test, expect } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import { openAdvisor, result, job, moreAction } from './fixtures/advisor-host.js';

test('capture real demo reply with provider logo, rich text and reviewed return controls', async ({ page }) => {
  const data = JSON.parse(await readFile(new URL('../../.venv/codex-install/real-advisor-demo.json', import.meta.url), 'utf8').catch(error => {
    if (error.code !== 'ENOENT') throw error;
    return JSON.stringify({ model: 'google/gemini-example', title: 'Classroom activity', answer: '# Compare two advisors\n\n1. **Frame** a product idea.\n2. Ask two models for advice.\n3. Discuss the differences.' });
  }));
  const { viewer } = await openAdvisor(page, { responses: { 'get_advisor_result:result-1': [result(data)] } });
  await expect(viewer.locator('#answer')).not.toBeEmpty();
  await expect(viewer.locator('#model-logo img')).toBeVisible();
  for (const width of [360, 900]) {
    await page.evaluate(width => document.querySelector('iframe').style.width = `${width}px`, width);
    await page.waitForTimeout(150);
    await page.setViewportSize({ width: 1000, height: Math.ceil(await viewer.locator('body').evaluate(body => body.getBoundingClientRect().height)) + 32 });
    await page.locator('iframe').screenshot({ path: `../.venv/codex-install/advisor-chat-${width}.png` });
  }
  await viewer.getByRole('button', { name: 'Share with Claude', exact: true }).click();
  await expect(viewer.locator('#handoff-status')).toContainText('Shared');
  await page.waitForTimeout(150);
  await page.setViewportSize({ width: 1000, height: Math.ceil(await viewer.locator('body').evaluate(body => body.getBoundingClientRect().height)) + 32 });
  await page.locator('iframe').screenshot({ path: '../.venv/codex-install/advisor-chat-return-900.png' });
});

// Review captures (not checks) of each viewer state at desktop and phone widths.
// ADVISOR_SHOTS names the output folder; it defaults under the ignored .venv.
const folder = process.env.ADVISOR_SHOTS || '../.venv/advisor-review';
const answer = '## Three options\n\n1. **Interview** five students first.\n2. Prototype the cheapest version.\n3. Compare both with a short survey.\n\nStart with interviews: they are fast and cheap.';
const recent = () => new Date(Date.now() - 42000).toISOString();

async function capture(page, viewer, name) {
  for (const width of [400, 900]) {
    await page.evaluate(width => document.querySelector('iframe').style.width = `${width}px`, width);
    await page.waitForTimeout(200);
    const height = Math.ceil(await viewer.locator('body').evaluate(body => body.getBoundingClientRect().height));
    await page.setViewportSize({ width: 1000, height: height + 32 });
    await page.locator('iframe').screenshot({ path: `${folder}/advisor-${name}-${width}.png` });
  }
}

test('capture running and streaming states', async ({ page }) => {
  const { viewer } = await openAdvisor(page, { initial: { content: [], structuredContent: job({ status: 'running' }) }, responses: {
    'get_advisor_result:result-1': [result({ status: 'running', answer: '', created_at: recent(), cost_usd: null }),
      { ...result({ status: 'running', answer: answer.slice(0, 120), created_at: recent(), cost_usd: null }), hold: 'stream' },
      { ...result({ status: 'running', answer: answer.slice(0, 120), created_at: recent(), cost_usd: null }), hold: 'never' }],
  } });
  await expect(viewer.locator('#job-status')).toContainText('Running');
  await capture(page, viewer, 'running');
  await page.evaluate(() => window.release('stream'));
  await expect(viewer.locator('#answer')).toContainText('Interview');
  await capture(page, viewer, 'streaming');
});

test('capture completed and follow-up states', async ({ page }) => {
  const { viewer } = await openAdvisor(page, { responses: { 'get_advisor_result:result-1': [result({ answer })] } });
  await expect(viewer.locator('#answer')).toContainText('Start with interviews');
  await capture(page, viewer, 'completed');
  await moreAction(viewer, 'Copy answer');
  await capture(page, viewer, 'more-menu');
  await viewer.getByText('More', { exact: true }).click();
  await viewer.getByLabel(/advisor/i).and(viewer.locator('textarea')).first().fill('What would the survey ask?');
  await capture(page, viewer, 'follow-up');
  await viewer.getByRole('button', { name: 'Share with Claude', exact: true }).click();
  await expect(viewer.locator('#handoff-status')).toContainText('Shared');
  await capture(page, viewer, 'shared');
});

test('capture failed state', async ({ page }) => {
  const { viewer } = await openAdvisor(page, { responses: { 'get_advisor_result:result-1': [result({
    status: 'failed', answer: answer.slice(0, 90), error: 'The advisor model did not complete; no automatic retry was made.', cost_usd: null,
  })] } });
  await expect(viewer.locator('#error')).toContainText('did not complete');
  await capture(page, viewer, 'failed');
});
