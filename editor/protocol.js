((root) => {
  const version = 1;
  function envelope(message) {
    return message && message.version === version && ['hello', 'ready', 'render', 'ack', 'error'].includes(message.type);
  }
  function validateRender(message, contract) {
    if (!envelope(message) || message.type !== 'render' || !Number.isSafeInteger(message.revision) || message.revision < 1 ||
        typeof message.slug !== 'string' || !/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(message.slug) || message.slug.length > 63 ||
        typeof message.text !== 'string' || new TextEncoder().encode(message.text).byteLength > contract.limits.text_bytes ||
        !Array.isArray(message.assets) || message.assets.length > contract.limits.files || !Array.isArray(message.paths) || message.paths.length > contract.limits.files) {
      throw new Error('Invalid preview message');
    }
    const part = new RegExp(`^${contract.path_component}$`);
    const paths = new Set(message.paths);
    if (paths.size !== message.paths.length) throw new Error('Duplicate preview path');
    for (const path of paths) {
      if (typeof path !== 'string' || path.length > contract.limits.path_length || path.split('/').length > contract.limits.path_depth || !path.split('/').every(value => part.test(value))) throw new Error('Invalid preview path');
      if (path !== 'text.md' && (!contract.media_extensions.includes('.' + path.split('.').pop()) || (path.includes('/') && !path.startsWith('media/')))) throw new Error('Unsupported preview asset');
    }
    let total = 0;
    const added = new Set();
    for (const asset of message.assets) {
      if (!asset || !paths.has(asset.path) || added.has(asset.path) || !(asset.bytes instanceof ArrayBuffer)) throw new Error('Invalid preview asset');
      const limit = asset.path === 'text.md' ? contract.limits.text_bytes : asset.path.endsWith('.svg') ? contract.limits.svg_bytes : contract.limits.media_bytes;
      if (asset.bytes.byteLength > limit) throw new Error('Preview asset exceeds file limit');
      total += asset.bytes.byteLength;
      added.add(asset.path);
    }
    if (total > contract.limits.group_bytes) throw new Error('Preview assets exceed group limit');
  }
  const api = { version, envelope, validateRender };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.CoursePreviewProtocol = api;
})(globalThis);
