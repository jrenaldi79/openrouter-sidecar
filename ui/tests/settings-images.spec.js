import { test, expect } from '@playwright/test';
import { openSettings, calls, settings, view } from './fixtures/settings-host.js';

test('keeps pricing visible while model context details collapse independently', async ({ page }) => {
  const { viewer } = await openSettings(page);
  await viewer.getByRole('button', { name: 'Models', exact: true }).click();
  await expect(viewer.locator('#general-info')).toContainText('$1 input');
  await expect(viewer.locator('#general-details')).not.toHaveAttribute('open', '');
  await expect(viewer.locator('#general-extra')).toBeHidden();
  await viewer.locator('#general-details summary').click();
  await expect(viewer.locator('#general-extra')).toContainText('4096');
  await viewer.locator('#general-details summary').click();
  await expect(viewer.locator('#general-extra')).toBeHidden();
  expect(await calls(page, 'set_sidecar_preferences')).toEqual([]);
});

test('Grok choices share general eligibility and autosave to their own default', async ({ page }) => {
  const row = { ...view().models[0], id: 'x-ai/grok-example', name: 'Grok Example', family: 'x-ai' };
  const { viewer } = await openSettings(page, { responses: { get_sidecar_settings: [settings({ models: [...view().models, row, { ...row, id: 'x-ai/grok-old', created: 1 }, { ...row, id: 'x-ai/grok:batch' }] })] } });
  await viewer.getByRole('button', { name: 'Models', exact: true }).click();
  await expect(viewer.locator('#general')).toContainText('Grok Example');
  await expect(viewer.locator('#x-ai')).not.toContainText('grok-old');
  await expect(viewer.locator('#x-ai')).not.toContainText('grok:batch');
  await viewer.locator('#brand-defaults > summary').click();
  await viewer.getByLabel('Grok / xAI default').selectOption('x-ai/grok-example');
  await expect(viewer.locator('#save-status')).toContainText('Saved');
  expect(await calls(page, 'set_sidecar_preferences')).toEqual([{ settings_id: 'settings-1', expected_revision: 1, family_defaults: { 'x-ai': 'x-ai/grok-example' } }]);
});

test('Images shows focused edits with a free comparison at narrow widths', async ({ page }) => {
  const { viewer } = await openSettings(page);
  await viewer.getByRole('button', { name: 'Images', exact: true }).click();
  await viewer.getByRole('tab', { name: 'Edit', exact: true }).click();
  const panel = viewer.locator('[data-panel="3"]');
  await expect(panel).toContainText('Keep the rest');
  await expect(panel).toContainText('Edited');
  await expect(panel).not.toContainText('mask');
  await expect(viewer.locator('#image-edit-panel img')).toHaveCount(2);
  await expect(panel.locator('.edit-comparison')).toBeVisible();
  for (const width of [320, 900]) {
    await page.evaluate(width => document.querySelector('iframe').style.width = `${width}px`, width);
    expect(await viewer.locator('body').evaluate(body => body.scrollWidth <= body.clientWidth)).toBe(true);
    await page.locator('iframe').screenshot({ path: `../.venv/codex-install/sidecar-images-${width}.png` });
  }
  for (const name of ['generate_image', 'edit_image', 'remask_image', 'run_sidecar_demo']) expect(await calls(page, name)).toEqual([]);
});
