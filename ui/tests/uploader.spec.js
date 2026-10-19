import { test, expect } from '@playwright/test';
import { ORIGIN, messages, openUploader, pngFile, routeUploads, ticket } from './fixtures/uploader-host.js';

const fileInput = viewer => viewer.locator('input[type=file]');

test('uploads a file, shows its asset id and tells the model', async ({ page }) => {
  const seen = await routeUploads(page);
  const viewer = await openUploader(page);
  await fileInput(viewer).setInputFiles(pngFile());
  await expect(viewer.getByText('img_00000000000000000000000000000001')).toBeVisible();
  const [call] = (await messages(page, 'tools/call')).map(m => m.params);
  expect(call).toMatchObject({ name: 'create_image_upload', arguments: { filename: 'room.png', mime: 'image/png', size_bytes: 64 } });
  expect(seen).toEqual([{ url: `${ORIGIN}/uploads/upl_1.9999999999.sig`, type: 'image/png', size: 64 }]);
  const [context] = await messages(page, 'ui/update-model-context');
  expect(context.params.content[0].text).toContain('img_00000000000000000000000000000001');
  expect(context.params.content[0].text).toContain('edit_image');
});

test('several uploads are all kept in the model context', async ({ page }) => {
  await routeUploads(page);
  const viewer = await openUploader(page);
  await fileInput(viewer).setInputFiles([pngFile('a.png'), pngFile('b.png')]);
  await expect(viewer.getByText(/^Uploaded as img_/)).toHaveCount(2);
  const contexts = await messages(page, 'ui/update-model-context');
  const last = contexts.at(-1).params.content[0].text;
  expect(last).toContain('a.png');
  expect(last).toContain('b.png');
});

test('rejects oversize and unsupported files before asking for a link', async ({ page }) => {
  await routeUploads(page);
  const viewer = await openUploader(page);
  await fileInput(viewer).setInputFiles([
    { name: 'huge.png', mimeType: 'image/png', buffer: Buffer.alloc(20 * 1024 * 1024 + 1) },
    { name: 'anim.gif', mimeType: 'image/gif', buffer: Buffer.alloc(10) }]);
  await expect(viewer.getByText(/larger than 20 MB/)).toBeVisible();
  await expect(viewer.getByText(/only PNG, JPEG or WebP/)).toBeVisible();
  expect(await messages(page, 'tools/call')).toEqual([]);
});

test('a network failure keeps the file for a retry with a fresh link', async ({ page }) => {
  const seen = await routeUploads(page, ['network', 'ok']);
  const viewer = await openUploader(page);
  await fileInput(viewer).setInputFiles(pngFile());
  await expect(viewer.getByText(/not uploaded/)).toBeVisible();
  await viewer.getByRole('button', { name: 'Retry room.png' }).click();
  await expect(viewer.getByText('Uploaded as img_00000000000000000000000000000002')).toBeVisible();
  expect(seen.map(s => s.url)).toEqual([`${ORIGIN}/uploads/upl_1.9999999999.sig`, `${ORIGIN}/uploads/upl_2.9999999999.sig`]);
});

test('server rejections are shown without a retry', async ({ page }) => {
  await routeUploads(page, [{ status: 400, error: 'The upload is not a readable PNG, JPEG or WebP image.' }]);
  const viewer = await openUploader(page);
  await fileInput(viewer).setInputFiles(pngFile());
  await expect(viewer.getByText('The upload is not a readable PNG, JPEG or WebP image.')).toBeVisible();
  await expect(viewer.getByRole('button', { name: /Retry/ })).toHaveCount(0);
});

test('refuses an upload link for another origin', async ({ page }) => {
  const seen = await routeUploads(page);
  const evil = ticket(1);
  evil.structuredContent.upload_url = 'https://evil.example/uploads/x';
  const viewer = await openUploader(page, { tickets: [evil] });
  await fileInput(viewer).setInputFiles(pngFile());
  await expect(viewer.getByText(/unexpected upload link/i)).toBeVisible();
  expect(seen).toEqual([]);
});
