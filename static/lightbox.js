((root) => {
  function create(dialog, onChange) {
    const image = dialog.querySelector('img');
    const listeners = new AbortController();
    let invoker = null;
    let opened = false;

    function open(trigger) {
      const source = trigger.querySelector('img');
      image.src = source.src;
      image.alt = source.alt;
      invoker = trigger;
      opened = true;
      dialog.showModal();
      onChange(true);
    }

    function closed() {
      if (dialog.open || !opened) return;
      opened = false;
      image.removeAttribute('src');
      if (invoker?.isConnected) invoker.focus();
      invoker = null;
      onChange(false);
    }

    function close() {
      if (dialog.open) dialog.close();
      closed();
    }

    dialog.addEventListener('close', closed, { signal: listeners.signal });
    dialog.addEventListener('click', event => {
      if (event.target === dialog || event.target === image) close();
    }, { signal: listeners.signal });

    return { open, close, dispose() { close(); listeners.abort(); image.removeAttribute('src'); invoker = null; } };
  }

  const api = { create };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.CourseLightbox = api;
})(globalThis);
