// Run with playwright-cli run-code --filename=scripts/check_media_behaviours.js
// against the local build. External destinations are intercepted: this tests
// native new-tab navigation without depending on a provider's availability.
async page => {
  const assert = (condition, message) => { if (!condition) throw new Error(message); };
  const errors = [];
  const onError = error => errors.push(error.message);
  page.on('pageerror', onError);
  const origin = await page.evaluate(() => location.origin);
  const external = route => {
    return route.request().url().startsWith(origin + '/') ? route.continue()
      : route.fulfill({ contentType: 'text/html', body: '<title>Original source</title>' });
  };
  await page.context().route('**/*', external);
  try {
    await page.reload();
    await page.locator('.inline-audio').first().waitFor();
    await page.mouse.move(2, 2);
    assert(await page.locator('[data-hover-audio]').evaluate(media => media.paused), 'Audio plays without interaction');
    assert(await page.locator('.inline-audio').first().innerText(), 'Static audio player has no label');
    const citations = page.locator('.annotation-bibliography');
    assert(await citations.count() > 0, 'No citations to check');
    assert(await citations.locator('a').count() > 0, 'No linked citations to check');
    const unwantedUnderlines = await page.locator('.annotation-toggle, .bibliography-label, .annotation-content, .inline-audio, .inline-playback, .inline-image').evaluateAll(elements =>
      elements.filter(el => getComputedStyle(el).textDecorationLine.includes('underline')).map(el => el.className));
    assert(unwantedUnderlines.length === 0, 'Non-source text is underlined: ' + unwantedUnderlines.join(', '));
    for (const selector of ['.annotation-bibliography a', '.inline-image', '.inline-playback', '.inline-audio']) {
      const target = page.locator(selector).first();
      await target.scrollIntoViewIfNeeded();
      await page.mouse.move(2, 2);
      if (selector === '.inline-playback') {
        await page.waitForFunction(() => {
          const video = document.querySelector('.inline-playback video');
          return !video.paused && video.currentTime > 0;
        });
        assert(await target.locator('video').evaluate(media => media.muted && media.loop && !media.controls), 'Inline video must loop silently');
      }
      await target.hover();
      if (selector === '.annotation-bibliography a') {
        assert(await target.evaluate(el => getComputedStyle(el).textDecorationLine.includes('underline')), 'Citation is not underlined on hover');
      }
      if (selector === '.inline-image' || selector === '.inline-playback') {
        await page.locator('#image-preview').waitFor({ state: 'visible' });
        const inline = await target.boundingBox();
        const zoom = await page.locator('#image-preview').boundingBox();
        assert(zoom.width > inline.width && zoom.height > inline.height, 'Hover does not enlarge the media');
      }
      if (selector === '.inline-playback' || selector === '.inline-audio') {
        const media = selector === '.inline-audio' ? '[data-hover-audio]' : '#image-preview video';
        if (await page.locator('.enable-hover-sound').isVisible()) await page.locator('.enable-hover-sound').click();
        await page.waitForFunction(selector => {
          const media = document.querySelector(selector);
          return !media.paused && !media.muted && media.volume > 0 && media.currentTime > 0;
        }, media);
        if (selector === '.inline-audio') {
          // Check the click handler itself: a real new tab can also trigger
          // blur/visibility cleanup and conceal a missing audio click handler.
          await target.evaluate(el => el.dispatchEvent(new Event('click', { bubbles: true })));
          assert(await page.locator(media).evaluate(el => el.paused), 'Audio click relies on window blur to stop playback');
        }
      }
      const href = await target.getAttribute('href');
      assert(/^https?:/.test(href), 'Missing original source URL');
      const opened = page.waitForEvent('popup');
      await target.click();
      const popup = await opened;
      await popup.waitForLoadState('domcontentloaded');
      assert(popup.url() === href, 'Click opens the wrong source');
      assert(await popup.evaluate(() => window.opener === null), 'Source tab retains an opener');
      await popup.close();
      await page.bringToFront();
      assert(await page.locator('#image-preview').isHidden(), 'Click leaves the zoom open');
      assert(await page.locator('[data-hover-audio]').evaluate(media => media.paused), 'Click leaves hover audio playing');
      assert(await page.locator('#image-preview video').evaluate(media => media.paused), 'Click leaves hover video playing');
    }
    assert(errors.length === 0, errors.join('\n'));
    return 'Passed: citation underline; image thumbnail/zoom; silent inline video loop; audible video/audio hover; static audio; original-source tabs; click cleanup.';
  } finally {
    page.off('pageerror', onError);
    await page.context().unroute('**/*', external);
  }
}
