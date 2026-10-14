async page => {
  const failures = [];
  page.on('pageerror', error => failures.push(error.message));
  await page.evaluate(async () => {
    localStorage.clear();
    const database = await new CourseDraftStorage.DraftStore().open();
    await new Promise((resolve, reject) => {
      const transaction = database.transaction(['drafts', 'blobs'], 'readwrite');
      for (const store of ['drafts', 'blobs']) transaction.objectStore(store).clear();
      transaction.oncomplete = resolve; transaction.onabort = () => reject(transaction.error);
    });
  });
  await page.locator('#template').click();
  await page.waitForFunction(() => !document.querySelector('#text').disabled);
  await page.locator('#path').fill('media/durable-motion.mp4');
  await page.locator('#attachment').setInputFiles('templates/group/media/example-motion.mp4');
  await page.locator('#attach').click();
  await page.waitForFunction(() => document.querySelector('#files').textContent.includes('media/durable-motion.mp4'));
  await page.locator('#text').fill('---\ntitle: Durable draft\n---\n[[motion|media/durable-motion.mp4]]');
  await page.waitForFunction(() => document.querySelector('#save').textContent === 'Text and attached media saved in this browser only.');
  await page.frameLocator('#preview').locator('.inline-playback video').waitFor();
  await page.reload();
  await page.locator('#template').click();
  await page.waitForFunction(() => !document.querySelector('#text').disabled);
  await page.locator('#restore').click();
  await page.waitForFunction(() => document.querySelector('#text').value.includes('Durable draft'));
  await page.waitForFunction(() => document.querySelector('#save').textContent === 'Text and attached media saved in this browser only.');
  const frame = page.frameLocator('#preview');
  await frame.locator('.inline-playback video').waitFor();
  if (!(await frame.locator('.inline-playback video').getAttribute('src')).startsWith('blob:')) throw new Error('Restored media did not render');
  await page.locator('#check').click();
  await page.waitForFunction(() => document.querySelector('#validation').textContent.includes('Known local checks passed'));

  await page.evaluate(() => {
    window.normalDraftSave = CourseDraftStorage.DraftStore.prototype.save;
    CourseDraftStorage.DraftStore.prototype.save = () => Promise.reject(new DOMException('Fixture full', 'QuotaExceededError'));
  });
  await page.locator('#text').fill('Unsaved text after quota failure');
  await page.waitForFunction(() => document.querySelector('#save').textContent.includes('Could not save'));
  await page.evaluate(() => {
    window.originalConfirm = window.confirm;
    window.confirm = message => { window.unsavedPrompt = message; return false; };
  });
  await page.locator('#template').click();
  if (!(await page.evaluate(() => window.unsavedPrompt)).includes('not been saved')) throw new Error('Unsaved draft guard was skipped');
  await page.evaluate(() => { window.confirm = window.originalConfirm; });
  if (await page.locator('#text').inputValue() !== 'Unsaved text after quota failure') throw new Error('Failed-save draft was discarded on group switch');
  if (!(await page.locator('#files').textContent()).includes('media/durable-motion.mp4')) throw new Error('Failed-save attachments were discarded');
  await page.evaluate(() => { CourseDraftStorage.DraftStore.prototype.save = window.normalDraftSave; });
  await page.locator('#text').fill('Recovered after quota failure. [[motion|media/durable-motion.mp4]]');
  await page.waitForFunction(() => document.querySelector('#save').textContent === 'Text and attached media saved in this browser only.');

  const storage = await page.evaluate(async () => {
    const store = new CourseDraftStorage.DraftStore('ddd-storage-check-' + Date.now());
    const blob = new Blob(['stored-media']);
    const asset = { path: 'media/file.png', blob, size: blob.size, sha256: await CourseDraft.digest(blob) };
    const payload = { version: 2, text: 'first', baseId: 'test', assets: [asset], missingPaths: [], deletions: [] };
    let revision = await store.save('a', payload, 0);
    const restored = await store.read('a');
    if (await restored.assets[0].blob.text() !== 'stored-media') throw new Error('Stored blob changed');
    const attempts = await Promise.allSettled([store.save('a', { ...payload, text: 'writer one' }, revision), store.save('a', { ...payload, text: 'writer two' }, revision)]);
    if (attempts.filter(result => result.status === 'fulfilled').length !== 1 || attempts.filter(result => result.reason?.name === 'DraftConflict').length !== 1) throw new Error('Concurrent write conflict was not detected');
    revision = (await store.read('a')).revision;
    const original = IDBObjectStore.prototype.put;
    let refuseMedia = true;
    IDBObjectStore.prototype.put = function (...arguments_) {
      if (this.name === 'blobs' && refuseMedia) throw new DOMException('Fixture quota full', 'QuotaExceededError');
      return original.apply(this, arguments_);
    };
    try {
      revision = await store.save('a', { ...payload, text: 'text under full media quota' }, revision);
      const replacement = new Blob(['replacement']);
      const next = { ...asset, blob: replacement, size: replacement.size, sha256: await CourseDraft.digest(replacement) };
      const rejected = await store.save('a', { ...payload, assets: [next] }, revision).then(() => false, () => true);
      if (!rejected) throw new Error('Quota fixture did not reject new media');
      const intact = await store.read('a');
      if (intact.text !== 'text under full media quota' || await intact.assets[0].blob.text() !== 'stored-media') throw new Error('Failed media save changed previous text or blob');
    } finally { IDBObjectStore.prototype.put = original; refuseMedia = false; }
    await store.save('b', payload, 0);
    const discarded = await store.discard('a', revision);
    const stale = await store.save('a', payload, revision).then(() => false, error => error.name === 'DraftConflict');
    if (!stale || discarded <= revision || !(await store.read('a')).discarded) throw new Error('Discard reset the concurrency revision');
    if (await (await store.read('b')).assets[0].blob.text() !== 'stored-media') throw new Error('Discard erased another draft’s shared media');
    const database = await store.open(); database.close();
    indexedDB.deleteDatabase(store.database);
    return { compareAndSwap: 'passed', mediaQuotaRollback: 'passed', textOnlySaveAtQuota: 'passed', sharedBlobRetention: 'passed' };
  });

  for (let i = 0; i < 3; i++) {
    await page.locator('#text').fill(`Repeated revision ${i}. [[motion|media/durable-motion.mp4]]`);
    await frame.getByText(`Repeated revision ${i}.`, { exact: false }).waitFor();
    if (await frame.locator('.entry').count() !== 1) throw new Error('Repeated render accumulated entries');
  }
  await page.locator('#width').selectOption('390');
  await page.setViewportSize({ width: 390, height: 844 });
  const widths = await page.evaluate(() => ({ editor: document.documentElement.scrollWidth, preview: document.querySelector('#preview').contentDocument.documentElement.scrollWidth }));
  if (widths.editor !== 390 || widths.preview !== 390) throw new Error('390 px overflow: ' + JSON.stringify(widths));
  await page.waitForFunction(() => document.querySelector('#save').textContent === 'Text and attached media saved in this browser only.');
  if (failures.length) throw new Error(failures.join('\n'));
  return { restoredTextAndMedia: 'passed', failedSaveSwitch: 'preserved', repeatedRenders: 'passed', mobileWidths: widths, storage };
}
