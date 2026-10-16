// Run with playwright-cli run-code --filename=scripts/check_hover_playback.js
// against a built preview containing the canonical group demonstration.
async page => {
  const audio = '.inline-audio';
  const video = '.inline-playback';
  const state = selector => page.evaluate(selector => {
    const media = document.querySelector(selector);
    return { playing: !media.paused, muted: media.muted, volume: media.volume };
  }, selector);
  const checkPlaying = async selector => {
    await page.waitForFunction(selector => !document.querySelector(selector).paused, selector);
    const result = await state(selector);
    if (result.muted || result.volume !== 1) throw new Error('Preview is silent');
  };
  await page.reload();
  await page.locator(audio).first().waitFor();
  // Force the same rejection a fresh browser can produce, without depending
  // on the test browser's profile or autoplay launch flags.
  await page.evaluate(() => {
    window.realMediaPlay = HTMLMediaElement.prototype.play;
    HTMLMediaElement.prototype.play = function () {
      if (this.matches('[data-hover-audio], #image-preview video')) {
        return Promise.reject(new DOMException('Blocked fixture', 'NotAllowedError'));
      }
      return window.realMediaPlay.call(this);
    };
  });
  for (const [target, media] of [[audio, '[data-hover-audio]'], [video, '#image-preview video']]) {
    await page.locator(target).first().hover();
    await page.locator('.enable-hover-sound').waitFor({ state: 'visible' });
    await page.evaluate(() => { HTMLMediaElement.prototype.play = window.realMediaPlay; });
    await page.locator('.enable-hover-sound').click();
    await checkPlaying(media);
    await page.locator(target).first().hover();
    await checkPlaying(media);
    await page.mouse.move(2, 2);
    if ((await state(media)).playing) throw new Error('Leaving did not stop playback');
    await page.locator(target).first().hover();
    await checkPlaying(media);
    await page.keyboard.press('Escape');
    if ((await state(media)).playing) throw new Error('Escape did not stop playback');
    await page.evaluate(() => {
      HTMLMediaElement.prototype.play = function () {
        if (this.matches('[data-hover-audio], #image-preview video')) {
          return Promise.reject(new DOMException('Blocked fixture', 'NotAllowedError'));
        }
        return window.realMediaPlay.call(this);
      };
    });
  }
  await page.evaluate(() => { HTMLMediaElement.prototype.play = window.realMediaPlay; });
  await page.locator(audio).first().hover();
  await checkPlaying('[data-hover-audio]');
  await page.locator(video).first().hover();
  await checkPlaying('#image-preview video');
  if ((await state('[data-hover-audio]')).playing) throw new Error('Audio continued under video');
  await page.evaluate(() => CourseSite.dispose());
  if ((await state('[data-hover-audio]')).playing || (await state('#image-preview video')).playing) throw new Error('Disposed media keeps playing');
  if (await page.locator('.enable-hover-sound').isVisible()) throw new Error('Disposed sound prompt remains');
  console.log('Passed: audio/video audible hover, blocked-playback recovery, leave, Escape, switching and disposal.');
}
