import { test, expect } from '@playwright/test';
import { openSettings, calls, settings, view, failure } from './fixtures/settings-host.js';
import { assertNoHandoff, openAdvisor, result } from './fixtures/advisor-host.js';
test.setTimeout(20000);

async function models(viewer) { await viewer.getByRole('button', { name: 'Models', exact: true }).click(); }


test('loads full catalog once, shows cohort and per-million pricing, autosaves successive choices', async ({ page }) => {
  const { viewer } = await openSettings(page);
  await expect(viewer.locator('#workspace')).toContainText('Class A');
  await models(viewer);
  await expect(viewer.locator('#general-info')).toContainText('$1 input');
  await expect(viewer.locator('#general-info')).toContainText('/ 1M tokens');
  expect(await calls(page, 'get_sidecar_settings')).toHaveLength(1);
  await viewer.getByLabel('General advisor default').selectOption('openai/example');
  await expect(viewer.locator('#save-status')).toContainText('Saved');
  await viewer.getByLabel('ChatGPT / OpenAI default').selectOption('openai/example');
  await expect(viewer.locator('#save-status')).toContainText('Saved');
  expect(await calls(page, 'set_sidecar_preferences')).toEqual([
    { settings_id: 'settings-1', expected_revision: 1, general_default: 'openai/example' },
    { settings_id: 'settings-1', expected_revision: 2, family_defaults: { openai: 'openai/example' } },
  ]);
  await assertNoHandoff(page);
});

test('initial full view avoids duplicate catalog request and settings reopens saved choices', async ({ page }) => {
  const { viewer } = await openSettings(page, { initial: { structuredContent: { mode: 'settings' }, ...settings({ general_default: 'openai/example', onboarding_completed: true }) } });
  await expect(viewer.getByLabel('General advisor default')).toHaveValue('openai/example');
  expect(await calls(page, 'get_sidecar_settings')).toHaveLength(0);
  await expect(viewer.locator('#title')).toHaveText('Sidecar settings');
});

test('revision conflict preserves the choice without automatically overwriting refreshed settings', async ({ page }) => {
  const { viewer } = await openSettings(page, { responses: { get_sidecar_settings: [settings(), settings({ revision: 2 })], set_sidecar_preferences: [failure('Conflict')] } });
  await models(viewer); await viewer.getByLabel('General advisor default').selectOption('openai/example');
  await expect(viewer.locator('#error')).toContainText('not saved');
  await expect(viewer.getByLabel('General advisor default')).toHaveValue('openai/example');
  await expect(viewer.locator('#warning')).toContainText('Review');
  expect(await calls(page, 'set_sidecar_preferences')).toHaveLength(1);
});

test('rejected save refreshes ownership and clears edits when account changed', async ({ page }) => {
  const { viewer } = await openSettings(page, { responses: { get_sidecar_settings: [settings(), settings({ settings_id: 'settings-2', general_default: 'qwen/qwen-example' })], set_sidecar_preferences: [failure('Sidecar settings changed or are unavailable for this sign-in. Refresh settings.')] } });
  await models(viewer); await viewer.getByLabel('General advisor default').selectOption('openai/example');
  await expect(viewer.getByLabel('General advisor default')).toHaveValue('qwen/qwen-example');
  await expect(viewer.locator('#warning')).toContainText('Account or workspace changed');
  expect(await calls(page, 'set_sidecar_preferences')).toHaveLength(1);
});

test('signed out offers login and manual refresh, with safe errors', async ({ page }) => {
  const { viewer } = await openSettings(page, { responses: { get_sidecar_settings: [failure('Sign in required sk-secret'), settings()], auth_login: [{ content: [] }] } });
  await expect(viewer.getByRole('button', { name: 'Sign in' })).toBeVisible();
  await expect(viewer.locator('body')).not.toContainText('sk-secret');
  await viewer.getByRole('button', { name: 'Sign in' }).click();
  await expect(viewer.locator('#status')).toContainText('Refresh');
  expect(await calls(page, 'get_sidecar_settings')).toHaveLength(1);
  await viewer.getByRole('button', { name: 'Refresh', exact: true }).click();
  await expect(viewer.locator('#workspace')).toContainText('workshop');
});

test('successful signed-out status confirms configured workspace without preference access', async ({ page }) => {
  const { viewer } = await openSettings(page, { responses: { get_sidecar_settings: [{ content: [], _meta: { sidecarSettings: { signed_in: false, workspace_id: 'workshop', cohort: 'Class A' } } }] } });
  await expect(viewer.locator('#workspace')).toContainText('Class A');
  await expect(viewer.locator('#workspace')).toContainText('Signed out');
  await expect(viewer.getByRole('button', { name: 'Sign in' })).toBeVisible();
  await models(viewer); await expect(viewer.getByLabel('General advisor default')).toBeDisabled();
  expect(await calls(page, 'run_sidecar_demo')).toEqual([]);
});

test('visual walkthrough is free and Finish only marks completion', async ({ page }) => {
  const { viewer } = await openSettings(page, { responses: { set_sidecar_preferences: [{ content: [], structuredContent: view({ revision: 2, onboarding_completed: true }) }], get_sidecar_settings: [settings(), settings({ revision: 2, onboarding_completed: true })] } });
  await viewer.getByRole('button', { name: 'Next', exact: true }).click();
  await viewer.getByRole('button', { name: 'Next', exact: true }).click();
  await viewer.getByRole('button', { name: 'Next', exact: true }).click();
  await viewer.getByRole('button', { name: 'Next', exact: true }).click();
  expect(await calls(page, 'run_sidecar_demo')).toEqual([]);
  await viewer.getByRole('button', { name: 'Finish setup' }).click();
  await expect(viewer.locator('#finish-status')).toContainText('completed');
  expect(await calls(page, 'set_sidecar_preferences')).toEqual([{ settings_id: 'settings-1', expected_revision: 1, onboarding_completed: true }]);
  await assertNoHandoff(page);
});


test('visual guide explains deliberate skills and controlled return without inference', async ({ page }) => {
  const { viewer } = await openSettings(page);
  await expect(viewer.locator('#workspace')).toContainText('workshop');
  await viewer.getByRole('button', { name: 'How it works', exact: true }).click();
  const guide = viewer.locator('[data-panel="2"]');
  await expect(guide).toContainText('skill');
  await expect(guide).toContainText('main chat');
  await expect(guide).toContainText('history');
  await expect(viewer.getByRole('button', { name: 'Images', exact: true })).toBeVisible();
  await expect(guide).toContainText('Save');
  await expect(viewer.getByRole('button', { name: /Run .*demo/ })).toHaveCount(0);
  await expect(viewer.getByRole('button', { name: 'Try it', exact: true })).toHaveCount(0);
  for (const name of ['run_sidecar_demo', 'send_advisor_message', 'generate_image', 'get_advisor_result', 'get_sidecar_demo']) expect(await calls(page, name)).toEqual([]);
  await assertNoHandoff(page);
});

test('navigation retains autosaved choices without extra writes', async ({ page }) => {
  const { viewer } = await openSettings(page);
  await models(viewer);
  await viewer.getByLabel('General advisor default').selectOption('openai/example');
  await expect(viewer.locator('#save-status')).toContainText('Saved');
  await viewer.getByRole('button', { name: 'How it works', exact: true }).click();
  await viewer.getByRole('button', { name: 'Finish', exact: true }).click();
  await expect(viewer.getByRole('button', { name: 'Finish setup' })).toBeEnabled();
  await models(viewer);
  await expect(viewer.getByLabel('General advisor default')).toHaveValue('openai/example');
  expect(await calls(page, 'set_sidecar_preferences')).toHaveLength(1);
});

test('host-limited app makes no fallback or paid calls', async ({ page }) => {
  const { viewer } = await openSettings(page, { capabilities: { message: { text: {} } } });
  await expect(viewer.locator('#error')).toContainText('host');
  expect(await calls(page, 'get_sidecar_settings')).toEqual([]);
  await assertNoHandoff(page);
});

test('hostile catalog values remain literal without image or script execution', async ({ page }) => {
  const hostile = '<img src="https://evil.test/pixel" onerror="window.pwned=true"><script>alert(1)</script>';
  const { viewer, requests } = await openSettings(page, { responses: { get_sidecar_settings: [settings({ cohort: hostile, models: [{ ...view().models[0], name: hostile }] })] } });
  await expect(viewer.locator('#workspace')).toContainText(hostile);
  await models(viewer);
  await expect(viewer.locator('#general')).toContainText(hostile);
  await expect(viewer.locator('#workspace img, #workspace script, #general img')).toHaveCount(0);
  expect(requests).toEqual([]);
  await assertNoHandoff(page);
});

test('dark theme and narrow screen retain usable models and visual guide', async ({ page }) => {
  const { viewer } = await openSettings(page);
  await expect(viewer.locator('#workspace')).toContainText('workshop');
  await page.evaluate(() => { document.querySelector('iframe').style.width = '320px'; window.notify('ui/notifications/host-context-changed', { theme: 'dark' }); });
  await expect(viewer.locator('html')).toHaveAttribute('data-theme', 'dark');
  await models(viewer);
  expect(await viewer.locator('body').evaluate(body => body.scrollWidth <= body.clientWidth)).toBe(true);
  await viewer.getByRole('button', { name: 'How it works', exact: true }).click();
  expect(await viewer.locator('body').evaluate(body => body.scrollWidth <= body.clientWidth)).toBe(true);
  await assertNoHandoff(page);
});

test('teardown disables future mutations and ignores late settings', async ({ page }) => {
  const { viewer } = await openSettings(page);
  await models(viewer);
  await page.evaluate(() => document.querySelector('iframe').contentWindow.postMessage({ jsonrpc: '2.0', id: 'teardown', method: 'ui/resource-teardown', params: {} }, '*'));
  await expect.poll(() => page.evaluate(() => window.hostMessages.some(m => m.id === 'teardown' && 'result' in m))).toBe(true);
  await expect(viewer.getByLabel('General advisor default')).toBeDisabled();
  expect(await calls(page, 'set_sidecar_preferences')).toEqual([]);
});

test('viewer settings shortcut requests settings without sending answer context', async ({ page }) => {
  const { viewer } = await openAdvisor(page, { responses: { 'get_advisor_result:result-1': [result()] } });
  await viewer.getByRole('button', { name: 'Settings', exact: true }).click();
  // The request reaches the host asynchronously (postMessage): wait for it.
  const sentMessages = () => page.evaluate(() => window.hostMessages.filter(message => message.method === 'ui/message'));
  await expect.poll(async () => (await sentMessages()).length).toBe(1);
  const requests = await sentMessages();
  expect(requests[0].params.content).toEqual([{ type: 'text', text: 'Open Sidecar settings using open_sidecar_settings with mode settings.' }]);
  expect(await page.evaluate(() => window.hostMessages.filter(message => message.method === 'ui/update-model-context'))).toEqual([]);
});

test('visual guide is legible at desktop width and exposes its handoff diagram', async ({ page }) => {
  await page.setViewportSize({ width: 960, height: 1600 });
  const { viewer } = await openSettings(page);
  await expect(viewer.locator('#workspace')).toContainText('workshop');
  await page.evaluate(() => { document.querySelector('iframe').style.width = '900px'; });
  await viewer.getByRole('button', { name: 'How it works', exact: true }).click();
  await expect(viewer.locator('.flow')).toBeVisible();
  expect(await viewer.locator('body').evaluate(body => body.scrollWidth <= body.clientWidth)).toBe(true);
  await expect.poll(async () => Math.abs(Math.min(620, await viewer.locator('main').evaluate(main => Math.ceil(main.getBoundingClientRect().height))) - await page.locator('iframe').evaluate(frame => parseFloat(frame.style.height)))).toBeLessThan(2);
  await page.locator('iframe').screenshot({ path: '../.venv/codex-install/sidecar-guide.png' });
  await models(viewer);
  await expect.poll(async () => Math.abs(Math.min(620, await viewer.locator('main').evaluate(main => Math.ceil(main.getBoundingClientRect().height))) - await page.locator('iframe').evaluate(frame => parseFloat(frame.style.height)))).toBeLessThan(2);
  await page.locator('iframe').screenshot({ path: '../.venv/codex-install/sidecar-models.png' });
});

test('setup offers the five open weight families, with no Claude or stale choices', async ({ page }) => {
  const created = Math.floor(Date.now() / 1000) - 86400;
  const approved = ['z-ai/glm-5', 'qwen/qwen3', 'moonshotai/kimi-k2', 'minimax/minimax-m2', 'deepseek/deepseek-v4'];
  const catalog = approved.map(id => ({ id, name: id, family: 'open_weight', created, pricing: {} }));
  catalog.push({ id: 'anthropic/claude-test', name: 'Claude', family: 'anthropic', created });
  catalog.push({ id: 'qwen/qwen-old', name: 'Old Qwen', family: 'open_weight', created: created - 365 * 86400 });
  catalog.push({ id: 'google/unknown-date', family: 'google' });
  catalog.push({ id: 'google/future', family: 'google', created: created + 5 * 86400 });
  catalog.push({ id: 'other/unauthorized', family: 'open_weight', created });
  const { viewer } = await openSettings(page, { responses: { get_sidecar_settings: [settings({ models: [...view().models, ...catalog] })] } });
  await models(viewer);
  await expect(viewer.getByLabel('Open Weight default')).toBeVisible();
  for (const id of approved) {
    await expect(viewer.locator('#open_weight option')).toContainText([id]);
    await expect(viewer.locator('#general')).toContainText(id);
  }
  await expect(viewer.locator('body')).not.toContainText('Claude');
  for (const id of ['claude-test', 'qwen-old', 'unknown-date', 'future', 'unauthorized']) await expect(viewer.locator('#general')).not.toContainText(id);
  await viewer.getByRole('button', { name: 'Finish', exact: true }).click();
  await expect(viewer.locator('body')).not.toContainText('Claude');
});

test('open weight default autosaves independently and legacy excluded defaults are not options', async ({ page }) => {
  const { viewer } = await openSettings(page, { responses: { get_sidecar_settings: [settings({ general_default: 'anthropic/claude-old', family_defaults: { google: 'google/example', openai: '', anthropic: 'anthropic/claude-old' } })] } });
  await models(viewer);
  await expect(viewer.locator('#general')).not.toContainText('anthropic');
  await expect(viewer.locator('#general-info')).toContainText('no longer eligible');
  await viewer.getByLabel('General advisor default').selectOption('qwen/qwen-example');
  await expect(viewer.locator('#save-status')).toContainText('Saved');
  await viewer.getByLabel('Open Weight default').selectOption('qwen/qwen-example');
  await expect(viewer.locator('#save-status')).toContainText('Saved');
  expect((await calls(page, 'set_sidecar_preferences'))[1]).toEqual({ settings_id: 'settings-1', expected_revision: 2, family_defaults: { open_weight: 'qwen/qwen-example' } });
});

test('inline model settings stay compact with pricing visible under each selection', async ({ page }) => {
  const { viewer } = await openSettings(page);
  await expect(viewer.locator('#workspace')).toContainText('workshop');
  await page.evaluate(() => { document.querySelector('iframe').style.width = '360px'; });
  await models(viewer);
  await expect(viewer.locator('#general-info')).toBeVisible();
  await expect.poll(() => page.locator('iframe').evaluate(frame => parseFloat(frame.style.height))).toBeLessThanOrEqual(620);
  const contentHeight = await viewer.locator('main').evaluate(main => main.getBoundingClientRect().height);
  await page.locator('iframe').screenshot({ path: '../.venv/codex-install/sidecar-compact-collapsed.png' });
  expect(contentHeight).toBeLessThan(850);
  await expect(viewer.locator('#general-info')).toBeVisible();
  await expect(viewer.locator('#general-info')).toContainText('$1 input');
  await expect.poll(() => page.locator('iframe').evaluate(frame => parseFloat(frame.style.height))).toBeLessThanOrEqual(620);
  expect(await calls(page, 'set_sidecar_preferences')).toEqual([]);
  await page.locator('iframe').screenshot({ path: '../.venv/codex-install/sidecar-compact-models.png' });
});
