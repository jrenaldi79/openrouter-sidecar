import { test, expect } from '@playwright/test';
import { result, openAdvisor, assertNoChatMessage, contexts, shareButton, SHARED_FULL } from './fixtures/advisor-host.js';

test.afterEach(async ({ page }) => assertNoChatMessage(page));

test('formats markdown as rich text while sharing the raw Markdown', async ({ page }) => {
  const markdown = '# A product idea\n\n**Strong evidence** and *a caveat*.\n\n1. Interview users\n2. Test a prototype\n\n```python\nprint("hello")\n```\n\n| Option | Cost |\n| --- | --- |\n| A | Low |';
  const { viewer } = await openAdvisor(page, { responses: { 'get_advisor_result:result-1': [result({ answer: markdown })] } });
  await expect(viewer.locator('#answer h1')).toHaveText('A product idea');
  await expect(viewer.locator('#answer strong')).toHaveText('Strong evidence');
  await expect(viewer.locator('#answer ol li')).toHaveCount(2);
  await expect(viewer.locator('#answer pre code')).toContainText('print("hello")');
  await expect(viewer.locator('#answer table')).toBeVisible();
  await shareButton(viewer).click();
  await expect(viewer.locator('#handoff-status')).toHaveText(SHARED_FULL);
  expect((await contexts(page))[0].params.content[0].text).toBe(`Advisor answer from example/advisor (result result-1):\n\n${markdown}`);
});

test('markdown cannot execute HTML or load remote images or javascript links', async ({ page }) => {
  const { viewer, requests } = await openAdvisor(page, { responses: { 'get_advisor_result:result-1': [result({ answer: '<img src="https://hostile.test/pixel" onerror="alert(1)">\n\n![secret](https://hostile.test/image)\n\n[unsafe](javascript:alert(1))' })] } });
  await expect(viewer.locator('#answer')).toContainText('<img');
  await expect(viewer.locator('#answer img, #answer script')).toHaveCount(0);
  expect(await viewer.locator('#answer a').evaluateAll(nodes => nodes.every(node => !node.hasAttribute('href')))).toBe(true);
  expect(requests).toEqual([]);
});
