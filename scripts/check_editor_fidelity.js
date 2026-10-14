async page => {
  const origin = await page.evaluate(() => location.origin);
  const prefix = '/course-preview/';
  const slug = 'ddd-abstract';
  const production = await page.context().newPage();
  const editor = await page.context().newPage();
  try {
  for (const target of [production, editor]) {
    await target.emulateMedia({ reducedMotion: 'reduce' });
    await target.route(origin + prefix + '**', async route => {
      let resource = route.request().url().slice((origin + prefix).length).split(/[?#]/)[0];
      if (resource === 'entries.json') { await route.fulfill({ json: [slug] }); return; }
      if (resource.startsWith(slug + '/')) resource = 'editor/template/' + resource.slice(slug.length + 1);
      const requestHeaders = { ...route.request().headers(), 'cache-control': 'no-cache' };
      delete requestHeaders['if-modified-since']; delete requestHeaders['if-none-match'];
      const response = await route.fetch({ url: origin + '/' + resource, headers: requestHeaders });
      const body = await response.body();
      // Pages supports byte ranges; Python's local static server does not.
      // Match that transport so HTTP and blob players have equal seekability.
      const headers = { ...response.headers(), 'accept-ranges': 'bytes' };
      const range = route.request().headers().range;
      const match = range && /^bytes=(\d+)-(\d*)$/.exec(range);
      if (match) {
        const start = Number(match[1]), end = match[2] ? Math.min(Number(match[2]), body.length - 1) : body.length - 1;
        if (start > end || start >= body.length) {
          await route.fulfill({ status: 416, headers: { 'content-range': 'bytes */' + body.length } }); return;
        }
        const partial = body.subarray(start, end + 1);
        await route.fulfill({ status: 206, headers: { ...headers,
          'content-range': `bytes ${start}-${end}/${body.length}`, 'content-length': String(partial.length) }, body: partial });
      } else await route.fulfill({ response, headers, body });
    });
  }
  await production.goto(origin + prefix);
  await editor.goto(origin + prefix + 'editor/');
  await editor.locator('#template').click();
  const preview = editor.frameLocator('#preview');
  await preview.locator('.entry-visual img').waitFor();
  await production.locator('.entry-visual img').waitFor();
  const measured = [];
  for (const width of [1280, 390]) {
    await production.setViewportSize({ width, height: 800 });
    await editor.setViewportSize({ width: width === 1280 ? 2280 : 430, height: 1000 });
    await editor.locator('#width').selectOption(String(width));
    // Align the iframe to whole device pixels so the capture excludes its scroll container.
    await editor.locator('#preview').evaluate((element, width) => {
      Object.assign(element.style, { position: 'fixed', inset: '0 auto auto 0', width: width + 'px', height: '800px', zIndex: '100' });
    }, width);
    for (const target of [production, preview.locator('body')]) {
      await target.evaluate(async () => {
        await document.fonts.ready;
        await Promise.all([...document.querySelectorAll('img')].map(image => image.decode().catch(() => {})));
        await Promise.all([...document.querySelectorAll('.entry audio,.entry video')].map(async media => {
          const loaded = new Promise((resolve, reject) => {
            media.addEventListener('canplaythrough', resolve, { once: true });
            media.addEventListener('error', () => reject(new Error('Could not load deterministic media')), { once: true });
          });
          media.preload = 'auto'; media.load(); await loaded;
          media.pause();
          const seeked = new Promise(resolve => media.addEventListener('seeked', resolve, { once: true }));
          media.currentTime = 0; await seeked;
        }));
        window.scrollTo(0, 0);
      });
    }
    const layout = () => [...document.querySelectorAll('.site-bar,.site-side,.entry-caption,.entry-text,.entry-visual,.entry-visual img,.inline-image,.inline-image img,.inline-audio,.inline-playback')].map(element => {
      const box = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      return { className: element.className, x: box.x, y: box.y,
        width: box.width, height: box.height,
        font: style.font, lineHeight: style.lineHeight, display: style.display,
        objectFit: style.objectFit, transform: style.transform,
        naturalWidth: element.naturalWidth, naturalHeight: element.naturalHeight };
    });
    const expected = await production.evaluate(layout);
    const actual = await preview.locator('body').evaluate(layout);
    if (JSON.stringify(expected) !== JSON.stringify(actual)) throw new Error('Production/preview layout differs at ' + width + ': ' + JSON.stringify({ expected, actual }));
    await production.mouse.move(0, 0); await editor.mouse.move(0, 0);
    // Native media controls animate independently of CSS screenshot suppression.
    await production.waitForTimeout(1000);
    await production.bringToFront();
    const siteImage = await production.screenshot({ animations: 'disabled', path: `.scratch/fidelity-site-${width}.png` });
    await editor.bringToFront();
    const previewImage = await editor.locator('#preview').screenshot({ animations: 'disabled', path: `.scratch/fidelity-preview-${width}.png` });
    // PNG encoding/metadata can differ even when the decoded pixels are identical.
    const difference = await production.evaluate(async images => {
      const decoded = await Promise.all(images.map(async data => {
        const image = new Image(); image.src = 'data:image/png;base64,' + data;
        await image.decode();
        const canvas = document.createElement('canvas');
        canvas.width = image.width; canvas.height = image.height;
        const context = canvas.getContext('2d'); context.drawImage(image, 0, 0);
        return { width: image.width, height: image.height,
          pixels: context.getImageData(0, 0, image.width, image.height).data };
      }));
      const [a, b] = decoded;
      if (a.width !== b.width || a.height !== b.height) return { dimensions: decoded.map(({width, height}) => ({width, height})) };
      const samples = [];
      let count = 0, minX = a.width, minY = a.height, maxX = -1, maxY = -1;
      for (let i = 0; i < a.pixels.length; i += 4) {
        if (a.pixels.slice(i, i + 4).every((value, channel) => value === b.pixels[i + channel])) continue;
        const x = (i / 4) % a.width, y = Math.floor(i / 4 / a.width);
        if (samples.length < 12) samples.push({ x, y, a: [...a.pixels.slice(i, i + 4)], b: [...b.pixels.slice(i, i + 4)] });
        count++; minX = Math.min(minX, x); minY = Math.min(minY, y);
        maxX = Math.max(maxX, x); maxY = Math.max(maxY, y);
      }
      return { count, samples, bounds: count ? { minX, minY, maxX, maxY } : null };
    }, [siteImage.toString('base64'), previewImage.toString('base64')]);
    if (difference.count !== 0) {
      const state = () => [...document.querySelectorAll('audio,video')].map(media => ({
        seekable: Array.from({length: media.seekable.length}, (_, i) => [media.seekable.start(i), media.seekable.end(i)]), duration: media.duration, time: media.currentTime, ready: media.readyState,
        buffered: Array.from({length: media.buffered.length}, (_, i) => [media.buffered.start(i), media.buffered.end(i)])
      }));
      throw new Error('Production/preview pixels differ at ' + width + ': ' + JSON.stringify({difference,
        siteMedia: await production.evaluate(state), previewMedia: await preview.locator('body').evaluate(state)}));
    }
    measured.push({ width, layout: 'equal', pixels: 'equal', projectPrefix: 'preserved' });
  }
  return measured;
  } finally {
    await production.close(); await editor.close();
  }
}
