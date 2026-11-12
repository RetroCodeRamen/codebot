import { h, replace } from '../dom.js';
import { get, post } from '../api.js';
import { every, errorBox } from '../ui.js';

export function mount(root, parts, params, app) {
  const list = h('div');
  root.appendChild(h('div', { class: 'page' }, h('div', { class: 'pagehead' }, h('h1', null, 'Inbox'), h('span', { class: 'muted' }, 'Finished runs and anything that needs a look')), list));
  async function draw() {
    try {
      const r = await get('/api/inbox');
      replace(list, r.items.length ? h('div', { class: 'panel' }, h('table', { class: 't' }, h('tbody', null, r.items.map(i => h('tr', null,
        h('td', null, h('span', { class: ['chip', { question: 'act', crashed: 'bad', finished: 'ok', benchmark: '' }[i.kind]] }, i.kind)),
        h('td', null, i.project ? h('a', { href: `#/p/${encodeURIComponent(i.project)}` }, h('b', { class: 'mono' }, i.project)) : h('a', { href: `#/bench/${encodeURIComponent(i.run)}` }, h('b', { class: 'mono' }, i.run))),
        h('td', null, i.text),
        h('td', null, i.kind === 'finished' || i.kind === 'benchmark' ? h('button', { type: 'button', on: { click: async () => { await post('/api/inbox/ack', { key: i.key }); draw(); } } }, 'Dismiss') : null))))))
        : h('div', { class: 'empty' }, 'Nothing needs you right now.'));
    } catch (e) { replace(list, errorBox(e)); }
  }
  return every(5000, draw);
}
