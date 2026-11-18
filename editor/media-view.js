((root) => {
  function create({ document, previews, actions, run }) {
    const $ = id => document.getElementById(id);
    // Name, source and insertion: what a writer needs for each media name.
    function mediaMetadata(entry, index, path) {
      const metadata = document.createElement('div'); metadata.dataset.index = index;
      for (const [field, label, type] of [['name', 'Name', 'text'], ['source', 'Source link', 'url']]) {
        const wrapper = document.createElement('label'); wrapper.textContent = label;
        const input = document.createElement('input'); input.type = type; input.dataset.field = field; input.value = entry[field];
        input.setAttribute('aria-label', `${label}: ${path}`); wrapper.append(input); metadata.append(wrapper);
      }
      const actions = document.createElement('div'); actions.className = 'row';
      const insert = document.createElement('button'); insert.textContent = 'Insert in text'; insert.dataset.action = 'insert'; actions.append(insert);
      metadata.append(actions);
      return metadata;
    }

    // The table row's exact file path and its removal: needed only to repair a row.
    function rowDetails(entry, index) {
      const row = document.createElement('div'); row.dataset.index = index;
      const label = document.createElement('label'); label.textContent = `File path for ${entry.name ? `[[${entry.name}]]` : 'this name'}`;
      const input = document.createElement('input'); input.value = entry.file; input.dataset.field = 'file'; label.append(input);
      const remove = document.createElement('button'); remove.textContent = 'Remove name'; remove.dataset.action = 'remove';
      row.append(label, remove);
      return row;
    }

    function more(...children) {
      const details = document.createElement('details'), summary = document.createElement('summary');
      summary.textContent = 'More'; details.append(summary, ...children);
      return details;
    }

    const useText = count => count === 0 ? 'not in the text yet' : count === 1 ? 'used once in the text' : `used ${count} times in the text`;


    function actionButton(label, action, error) {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = label;
      button.addEventListener('click', () => error ? run(action, error) : action());
      return button;
    }

    function fileActions(path, file, error) {
      const controls = document.createElement('div');
      controls.className = 'row';
      if (!file) return controls;
      controls.append(actionButton('Replace file', () => actions.replace(path)));
      if (file.blob) controls.append(actionButton('Download file', () => actions.download(file.blob, path.split('/').pop())));
      controls.append(actionButton('Remove file', () => actions.remove(path), error));
      return controls;
    }

    function card({ path, file, entries, uses }, tableError) {
      const item = document.createElement('li');
      item.dataset.path = path;
      const error = document.createElement('p');
      error.className = 'error';
      error.dataset.mediaError = '';
      error.setAttribute('role', 'status');
      if (file) item.append(previews.element(path, file));
      const heading = document.createElement('p');
      heading.className = 'media-file';
      heading.textContent = `${path} · ${file ? useText(uses) : 'file missing'}`;
      item.append(heading);
      for (const { entry, index } of entries) item.append(mediaMetadata(entry, index, path));
      if (!entries.length && !tableError) item.append(actionButton('Name this media', () => actions.name(path), error));
      item.append(more(fileActions(path, file, error), ...entries.map(({ entry, index }) => rowDetails(entry, index))), error);
      return item;
    }

    function render(inventory, tableError) {
      $('visual-preview').replaceChildren(...(inventory.visual.file ? [previews.element('visual.svg', inventory.visual.file)] : []));
      $('visual-metadata').replaceChildren(...inventory.visual.entries.map(({ entry, index }) => {
        const block = document.createElement('div');
        block.append(mediaMetadata(entry, index, 'visual.svg'), more(rowDetails(entry, index)));
        return block;
      }));
      $('media-rows').replaceChildren(...inventory.items.map(item => card(item, tableError)));
      previews.retain(new Set(['visual.svg', ...inventory.items.map(item => item.path)]));
    }

    return { render };
  }

  const api = { create };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.CourseMediaView = api;
})(globalThis);
