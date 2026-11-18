((root) => {
  async function fetchBlob(url, limit, fetchResource = root.fetch) {
    const response = await fetchResource(url, {
      cache: 'no-store', redirect: 'error', signal: AbortSignal.timeout(30000),
    });
    if (!response.ok) throw new Error(`Could not load ${url.pathname || url}`);
    if (!response.body) throw new Error('This browser cannot stream file downloads');
    const reader = response.body.getReader();
    const chunks = [];
    let size = 0;
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        size += value.byteLength;
        if (size > limit) throw new Error('Downloaded file exceeds its byte limit');
        chunks.push(value);
      }
    } catch (error) {
      try { await reader.cancel(); } catch { /* Preserve the download failure if cancellation also fails. */ }
      throw error;
    } finally {
      reader.releaseLock?.();
    }
    return new Blob(chunks);
  }

  const api = { fetchBlob };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.CourseDownload = api;
})(globalThis);
