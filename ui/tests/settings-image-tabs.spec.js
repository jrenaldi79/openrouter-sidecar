import { test, expect } from '@playwright/test';
import { openSettings, calls } from './fixtures/settings-host.js';

test('Create and Edit are distinct keyboard accessible previews with no model calls', async ({ page }) => {
  const { viewer } = await openSettings(page);
  await viewer.getByRole('button', { name: 'Images', exact: true }).click();
  const create = viewer.getByRole('tab', { name: 'Create', exact: true });
  const edit = viewer.getByRole('tab', { name: 'Edit', exact: true });
  await expect(create).toHaveAttribute('aria-selected', 'true');
  await expect(viewer.locator('#image-create-panel')).toContainText('Turn an idea into an image.');
  await expect(viewer.locator('#image-edit-panel')).toBeHidden();
  await create.focus();
  await create.press('ArrowRight');
  await expect(edit).toBeFocused();
  await expect(edit).toHaveAttribute('aria-selected', 'true');
  // Hovering the selected tab keeps the selected (Northwestern purple) fill.
  await edit.hover();
  await expect(edit).toHaveCSS('background-color', 'rgb(78, 42, 132)');
  await expect(viewer.locator('#image-create-panel')).toBeHidden();
  await expect(viewer.getByRole('slider')).toBeVisible();
  await edit.press('Home');
  await expect(create).toBeFocused();
  await expect(viewer.locator('#image-create-panel')).toBeVisible();
  for (const name of ['generate_image', 'edit_image', 'run_sidecar_demo', 'set_sidecar_preferences']) {
    expect(await calls(page, name)).toEqual([]);
  }
});
