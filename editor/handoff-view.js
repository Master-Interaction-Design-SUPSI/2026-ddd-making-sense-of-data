((root) => {
  function create({ document, container, effects, run }) {
    function externalLink(action) {
      const link = document.createElement('a');
      link.href = action.url;
      link.target = '_blank';
      link.rel = 'noopener';
      link.textContent = action.label;
      return link;
    }

    async function compare(item, result) {
      item.querySelector('details')?.remove();
      result.textContent = 'Comparing…';
      const comparison = await effects.compare();
      if (!comparison) return;
      result.textContent = comparison.message;
      if (comparison.source === undefined) return;
      const details = document.createElement('details');
      const summary = document.createElement('summary');
      const source = document.createElement('pre');
      summary.textContent = 'Text on GitHub';
      source.textContent = comparison.source;
      details.append(summary, source);
      item.append(details);
    }

    async function activate(action, item, result) {
      if (action.kind === 'compare') return compare(item, result);
      if (action.kind === 'download') return effects.download(action.blob, action.name);
      if (action.kind === 'copy') {
        await effects.copy(action.text);
        result.textContent = 'Copied. Paste it on GitHub.';
      }
    }

    function step({ text, actions }) {
      const item = document.createElement('li');
      const check = document.createElement('input');
      check.type = 'checkbox';
      const label = document.createElement('label');
      label.append(check, ` ${text}`);
      const controls = document.createElement('div');
      controls.className = 'row';
      const result = document.createElement('p');
      result.setAttribute('role', 'status');
      for (const action of actions) {
        if (action.kind === 'link') { controls.append(externalLink(action)); continue; }
        const button = document.createElement('button');
        button.type = 'button';
        button.textContent = action.label;
        button.addEventListener('click', () => run(() => activate(action, item, result), result));
        controls.append(button);
      }
      item.append(label, controls, result);
      return item;
    }

    return { render(steps) { container.replaceChildren(...steps.map(step)); } };
  }

  const api = { create };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.CourseHandoffView = api;
})(globalThis);
