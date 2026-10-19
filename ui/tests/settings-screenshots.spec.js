import { test, expect } from '@playwright/test';
import { openSettings, settings } from './fixtures/settings-host.js';

// Review captures, not assertions. Skipped under CI=1 (playwright.config.js).
// REVIEW_DIR picks the output folder, e.g. REVIEW_DIR=test-results/review/after.
const dir = process.env.REVIEW_DIR ?? 'test-results/review';
test.setTimeout(60000);

async function capture(page, viewer, name) {
  await page.evaluate(() => window.notify('ui/notifications/host-context-changed', { displayMode: 'fullscreen' }));
  await page.waitForTimeout(150);
  const height = Math.ceil(await viewer.locator('main').evaluate(main => main.getBoundingClientRect().height)) + 1;
  await page.setViewportSize({ width: 1000, height: height + 32 });
  await page.evaluate(height => { document.querySelector('iframe').style.height = `${height}px`; }, height);
  await viewer.locator('body').evaluate(() => { document.documentElement.scrollTop = 0; });
  await page.locator('iframe').screenshot({ path: `${dir}/${name}.png` });
}

for (const theme of ['light', 'dark']) for (const width of [400, 900]) {
  test(`capture onboarding and settings, ${theme} ${width}px`, async ({ page }) => {
    const { viewer } = await openSettings(page);
    await expect(viewer.locator('#workspace')).toContainText('workshop');
    await page.evaluate(({ width, theme }) => {
      document.querySelector('iframe').style.width = `${width}px`;
      window.notify('ui/notifications/host-context-changed', { theme });
    }, { width, theme });
    for (const [name, label] of [['1-setup', 'Setup'], ['2-models', 'Models'], ['3-advisor', 'How it works'], ['4-images', 'Images'], ['6-finish', 'Finish']]) {
      await viewer.getByRole('button', { name: label, exact: true }).click();
      await capture(page, viewer, `${name}-${theme}-${width}`);
      if (name === '4-images') {
        await viewer.getByRole('tab', { name: 'Edit', exact: true }).click();
        await capture(page, viewer, `5-images-edit-${theme}-${width}`);
        await viewer.getByRole('tab', { name: 'Create', exact: true }).click();
      }
    }
    await viewer.getByRole('button', { name: 'Models', exact: true }).click();
    await viewer.locator('#general-details summary').click();
    await viewer.locator('#brand-defaults > summary').click();
    await capture(page, viewer, `2b-models-details-${theme}-${width}`);
  });

  test(`capture saved settings view, ${theme} ${width}px`, async ({ page }) => {
    const initial = { structuredContent: { mode: 'settings' }, ...settings({ general_default: 'openai/example', onboarding_completed: true }) };
    const { viewer } = await openSettings(page, { initial });
    await expect(viewer.locator('#title')).toHaveText('Sidecar settings');
    await page.evaluate(({ width, theme }) => {
      document.querySelector('iframe').style.width = `${width}px`;
      window.notify('ui/notifications/host-context-changed', { theme });
    }, { width, theme });
    await capture(page, viewer, `7-settings-${theme}-${width}`);
  });
}

test('capture signed-out and missing-model states', async ({ page }) => {
  const signedOut = { get_sidecar_settings: [{ content: [], _meta: { sidecarSettings: { signed_in: false, workspace_id: 'workshop', cohort: 'Class A' } } }] };
  const { viewer } = await openSettings(page, { responses: signedOut });
  await expect(viewer.locator('#workspace')).toContainText('Signed out');
  await page.evaluate(() => { document.querySelector('iframe').style.width = '400px'; });
  await capture(page, viewer, '8-signed-out-light-400');
  const missing = await openSettings(page, { responses: { get_sidecar_settings: [settings({ general_default: null })] } });
  await expect(missing.viewer.locator('#workspace')).toContainText('workshop');
  await page.evaluate(() => { document.querySelector('iframe').style.width = '900px'; });
  await capture(page, missing.viewer, '9-needs-model-light-900');
  await missing.viewer.getByRole('button', { name: 'Models', exact: true }).click();
  await capture(page, missing.viewer, '9b-needs-model-models-light-900');
});
