import { test, expect } from '@playwright/test';
import { openSettings, calls, settings } from './fixtures/settings-host.js';

const step = (viewer, name) => viewer.getByRole('button', { name, exact: true }).click();
const noSideScroll = viewer => viewer.locator('body').evaluate(body => body.scrollWidth <= body.clientWidth);

test('brand defaults stay collapsed behind a count until opened, then autosave', async ({ page }) => {
  const { viewer } = await openSettings(page);
  await step(viewer, 'Models');
  await expect(viewer.getByLabel('General advisor default')).toBeVisible();
  await expect(viewer.locator('#brand-defaults')).not.toHaveAttribute('open', '');
  await expect(viewer.locator('#brand-count')).toHaveText('1 set');
  await expect(viewer.getByLabel('ChatGPT / OpenAI default')).toBeHidden();
  await viewer.locator('#brand-defaults > summary').click();
  await viewer.getByLabel('ChatGPT / OpenAI default').selectOption('openai/example');
  await expect(viewer.locator('#save-status')).toContainText('Saved');
  await expect(viewer.locator('#brand-count')).toHaveText('2 set');
  expect(await calls(page, 'set_sidecar_preferences')).toEqual([{ settings_id: 'settings-1', expected_revision: 1, family_defaults: { openai: 'openai/example' } }]);
});

test('an ineligible saved brand default opens brand defaults so it can be fixed', async ({ page }) => {
  const stale = settings({ family_defaults: { google: 'google/retired', openai: '', open_weight: '' } });
  const { viewer } = await openSettings(page, { responses: { get_sidecar_settings: [stale] } });
  await step(viewer, 'Models');
  await expect(viewer.locator('#brand-defaults')).toHaveAttribute('open', '');
  await expect(viewer.locator('#google-info')).toContainText('no longer eligible');
});

test('every step has one primary action in the same place', async ({ page }) => {
  const { viewer } = await openSettings(page);
  await expect(viewer.locator('#workspace')).toContainText('workshop');
  for (const name of ['Setup', 'Models', 'How it works', 'Images']) {
    await step(viewer, name);
    await expect(viewer.locator('button.primary:visible')).toHaveCount(1);
    await expect(viewer.locator('.bottom-nav #next')).toBeVisible();
  }
  await step(viewer, 'Finish');
  await expect(viewer.locator('button.primary:visible')).toHaveCount(1);
  await expect(viewer.locator('#next')).toBeHidden();
  await expect(viewer.locator('.bottom-nav').getByRole('button', { name: 'Finish setup' })).toBeEnabled();
});

test('account line stays in the header on every step without a ready-status label', async ({ page }) => {
  const { viewer } = await openSettings(page);
  await expect(viewer.locator('header #workspace')).toContainText('Class A');
  await expect(viewer.locator('#status')).toHaveText('');
  await step(viewer, 'Images');
  await expect(viewer.locator('#workspace')).toBeVisible();
});

test('signed out is a calm notice with Sign in as the primary action', async ({ page }) => {
  const signedOut = { content: [], _meta: { sidecarSettings: { signed_in: false, workspace_id: 'workshop', cohort: 'Class A' } } };
  const { viewer } = await openSettings(page, { responses: { get_sidecar_settings: [signedOut] } });
  await expect(viewer.locator('#workspace')).toContainText('Signed out');
  await expect(viewer.locator('#error')).toHaveText('');
  await expect(viewer.locator('#warning')).toContainText('Sign in');
  await expect(viewer.getByRole('button', { name: 'Sign in' })).toHaveClass(/primary/);
  await expect(viewer.getByRole('button', { name: 'Next', exact: true })).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)');
});

test('every step fits a 400px chat column without sideways scroll, light and dark', async ({ page }) => {
  const { viewer } = await openSettings(page);
  await expect(viewer.locator('#workspace')).toContainText('workshop');
  for (const theme of ['light', 'dark']) {
    await page.evaluate(theme => { document.querySelector('iframe').style.width = '400px'; window.notify('ui/notifications/host-context-changed', { theme }); }, theme);
    for (const name of ['Setup', 'Models', 'How it works', 'Images', 'Finish']) {
      await step(viewer, name);
      expect(await noSideScroll(viewer)).toBe(true);
    }
    await step(viewer, 'Models');
    await viewer.locator('#brand-defaults').evaluate(details => { details.open = true; });
    expect(await noSideScroll(viewer)).toBe(true);
  }
});
