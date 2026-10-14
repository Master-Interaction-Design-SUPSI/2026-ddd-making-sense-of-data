(async () => {
  const contract = await fetch('../static/contract.json').then(response => response.json());
  const assets = new Map();
  let revision = 0;
  let slug = null;
  const send = message => parent.postMessage({ version: 1, ...message }, location.origin);
  const mime = path => ({ svg: 'image/svg+xml', png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg',
    webp: 'image/webp', gif: 'image/gif', avif: 'image/avif', mp4: 'video/mp4', webm: 'video/webm', mov: 'video/quicktime',
    mp3: 'audio/mpeg', m4a: 'audio/mp4', wav: 'audio/wav', ogg: 'audio/ogg', aac: 'audio/aac', flac: 'audio/flac' })[path.split('.').pop()] || 'application/octet-stream';
  function clear() {
    CourseSite.dispose();
    document.getElementById('entries').replaceChildren();
    for (const asset of assets.values()) URL.revokeObjectURL(asset.url);
    assets.clear();
  }
  window.addEventListener('message', async event => {
    if (event.source !== parent || event.origin !== location.origin) return;
    const message = event.data;
    if (CoursePreviewProtocol.envelope(message) && message.type === 'hello') { send({ type: 'ready' }); return; }
    if (!CoursePreviewProtocol.envelope(message) || message.type !== 'render') return;
    try {
      CoursePreviewProtocol.validateRender(message, contract);
      if (message.revision <= revision) return;
      revision = message.revision;
      CourseSite.dispose();
      document.getElementById('entries').replaceChildren();
      if (slug !== message.slug) clear();
      slug = message.slug;
      for (const [path, asset] of assets) {
        if (!message.paths.includes(path) || message.assets.some(next => next.path === path)) {
          URL.revokeObjectURL(asset.url);
          assets.delete(path);
        }
      }
      let total = message.assets.reduce((sum, asset) => sum + asset.bytes.byteLength, 0);
      total += [...assets.values()].reduce((sum, asset) => sum + asset.size, 0);
      if (total > contract.limits.group_bytes) throw new Error('Preview exceeds group limit');
      for (const asset of message.assets) {
        assets.set(asset.path, { size: asset.bytes.byteLength, url: URL.createObjectURL(new Blob([asset.bytes], { type: mime(asset.path) })) });
      }
      await CourseSite.start({
        loadEntries: async () => [{ folder: slug, text: message.text, visual: message.paths.includes('visual.svg') ? 'visual.svg' : null }],
        resolveResource: (path, kind) => kind === 'link' && path.endsWith('.svg') ? false : assets.get(path)?.url || false,
      });
      if (message.revision === revision) send({ type: 'ack', revision });
    } catch (error) {
      send({ type: 'error', revision: message?.revision, message: error.message });
    }
  });
  window.addEventListener('pagehide', clear);
  send({ type: 'ready' });
})().catch(error => { document.getElementById('entries').textContent = error.message; });
