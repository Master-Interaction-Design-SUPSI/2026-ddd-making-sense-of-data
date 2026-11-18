((root) => {
  function validateSvg(source, path, contract, Parser = root.DOMParser) {
    const svg = new Parser().parseFromString(source, 'image/svg+xml');
    const element = svg.documentElement;
    if (svg.querySelector('parsererror') || element.localName !== 'svg' || element.namespaceURI !== 'http://www.w3.org/2000/svg') {
      throw new Error(`${path}: export a valid SVG image`);
    }
    if (path !== 'visual.svg') return;
    if (element.getAttribute('viewBox')?.trim().split(/[\s,]+/).map(Number).join(',') !== contract.visual_viewbox.join(',')) {
      throw new Error('visual.svg: export a valid 1920 × 1080 SVG');
    }
    for (const [name, expected] of [['width', 1920], ['height', 1080]]) {
      if (element.hasAttribute(name) && Number(element.getAttribute(name).trim().replace(/px$/, '')) !== expected) {
        throw new Error(`visual.svg: ${name} must be ${expected} or omitted`);
      }
    }
  }

  async function validate(draft, missingAttachments, contract, loadBlob) {
    if (missingAttachments.size) throw Object.assign(new Error('Reattach or explicitly remove these restored media files first: ' + [...missingAttachments].join(', ')), { field: 'media-heading' });
    const result = draft.validate();
    const changes = await draft.changes();
    for (const [path, file] of result.files) {
      if (!path.endsWith('.svg')) continue;
      const blob = file.blob || await loadBlob(file.url, file);
      const source = new TextDecoder('utf-8', { fatal: true }).decode(await blob.arrayBuffer());
      validateSvg(source, path, contract);
    }
    return { result, changes };
  }

  const api = { validateSvg, validate };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.CourseReview = api;
})(globalThis);
