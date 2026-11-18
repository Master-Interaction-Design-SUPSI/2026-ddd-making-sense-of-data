((root) => {
  const videos = ['mp4', 'webm', 'mov'], sounds = ['mp3', 'm4a', 'wav', 'ogg', 'aac', 'flac'];
  const tagFor = path => {
    const extension = path.split('.').pop().toLowerCase();
    return videos.includes(extension) ? 'video' : sounds.includes(extension) ? 'audio' : 'img';
  };

  // One preview element per file path. The media list is redrawn on every keystroke, so an unchanged
  // file keeps its element: images do not flicker and playback does not stop. A file held only in this
  // browser gets an object URL, released once the file is replaced or leaves the draft.
  function create(document, URL) {
    const shown = new Map();
    const release = entry => { if (entry.objectUrl) URL.revokeObjectURL(entry.objectUrl); };

    function element(path, file) {
      const source = file.url || file.blob;
      const current = shown.get(path);
      if (current?.source === source) return current.element;
      if (current) release(current);
      const tag = tagFor(path), node = document.createElement(tag);
      const objectUrl = file.url ? null : URL.createObjectURL(file.blob);
      if (tag === 'img') node.alt = '';
      else { node.controls = true; node.preload = 'metadata'; }
      node.className = 'media-preview'; node.src = objectUrl || String(file.url);
      shown.set(path, { source, element: node, objectUrl });
      return node;
    }

    function retain(paths) {
      for (const [path, entry] of shown) if (!paths.has(path)) { release(entry); shown.delete(path); }
    }

    return { element, retain };
  }

  const api = { create };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.CourseMediaPreview = api;
})(globalThis);
