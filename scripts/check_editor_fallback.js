async page => {
  // A fresh registry is empty; give the editor one live group so it can start from the template.
  await page.route('**/editor/groups.json', async route => {
    const response = await route.fetch();
    const policy = await response.json();
    policy.groups = [{ slug: 'check-group', repository: 'course/check-group', branch: 'main', exclude: [], state: 'live', editable: true, contract_version: 2 }];
    await route.fulfill({ response, json: policy });
  });
  await page.reload();
  await page.evaluate(() => {
    window.normalStoreOpen = CourseDraftStorage.DraftStore.prototype.open;
    CourseDraftStorage.DraftStore.prototype.open = () => Promise.reject(new DOMException('Fixture storage denied', 'SecurityError'));
    window.normalTextSave = Storage.prototype.setItem;
    window.refuseTextSave = true;
    Storage.prototype.setItem = function (...arguments_) {
      if (window.refuseTextSave) throw new DOMException('Fixture quota full', 'QuotaExceededError');
      return window.normalTextSave.apply(this, arguments_);
    };
  });
  await page.locator('#template').click();
  await page.waitForFunction(() => document.querySelector('#save').textContent.includes('Text-only fallback'));
  await page.locator('#text').fill('First fallback edit fails quota');
  await page.waitForFunction(() => document.querySelector('#save').textContent.includes('Could not save'));
  await page.evaluate(() => { window.refuseTextSave = false; });
  await page.locator('#text').fill('Newer text-only backup');
  await page.waitForFunction(() => document.querySelector('#save').textContent === 'Text saved in this browser only. Reattach media after reload.');
  const key = await page.evaluate(() => Object.keys(localStorage)[0]);
  if (await page.evaluate(key => JSON.parse(localStorage.getItem(key)).text, key) !== 'Newer text-only backup') throw new Error('Fallback quota recovery failed');
  await page.locator('#path').fill('media/fallback-detail.svg');
  await page.locator('#attachment').setInputFiles('templates/group/media/example-detail.svg');
  await page.locator('#attach').click();
  await page.waitForFunction(() => document.querySelector('#files').textContent.includes('media/fallback-detail.svg'));
  await page.waitForFunction(() => document.querySelector('#save').textContent === 'Text saved in this browser only. Reattach media after reload.');
  await page.evaluate(() => {
    CourseDraftStorage.DraftStore.prototype.open = window.normalStoreOpen;
    Storage.prototype.setItem = window.normalTextSave;
  });
  await page.locator('#template').click();
  await page.waitForFunction(() => document.querySelector('#save').textContent.includes('Two saved drafts'));
  await page.locator('#restore').click();
  await page.waitForFunction(() => document.querySelector('#save').textContent === 'Text and attached media saved in this browser only.');
  if (await page.evaluate(key => JSON.parse(localStorage.getItem(key)).text, key) !== 'Newer text-only backup') throw new Error('Restoring durable draft erased the newer fallback');
  await page.locator('#template').click();
  await page.waitForFunction(() => !document.querySelector('#fallback-restore').hidden);
  await page.locator('#fallback-restore').click();
  await page.waitForFunction(() => document.querySelector('#text').value === 'Newer text-only backup');
  await page.waitForFunction(() => document.querySelector('#save').textContent.includes('Reattach or discard missing media'));
  const saved = await page.evaluate(async key => {
    const record = await new CourseDraftStorage.DraftStore().read(key);
    return { text: record.text, missingPaths: record.missingPaths, legacy: localStorage.getItem(key) };
  }, key);
  if (saved.text !== 'Newer text-only backup' || !saved.missingPaths.includes('media/fallback-detail.svg') || saved.legacy !== null) throw new Error('Explicit fallback migration lost text or missing media');
  await page.locator('#files li').filter({ hasText: 'media/fallback-detail.svg' }).getByRole('button', { name: 'Discard missing attachment' }).click();
  await page.waitForFunction(() => document.querySelector('#save').textContent === 'Text and attached media saved in this browser only.');
  return { deniedMediaStorage: 'usable text fallback', fallbackQuotaRetry: 'passed', coexistingDrafts: 'preserved', explicitMigration: 'passed', missingAttachmentDiscard: 'passed' };
}
