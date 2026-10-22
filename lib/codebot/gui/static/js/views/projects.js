import { h, replace, when } from '../dom.js';
import { get } from '../api.js';
import { every, errorBox } from '../ui.js';
import { stateChip, stopText } from '../components.js';
import { progress } from './home.js';

export function mount(root, parts, params, app) {
  let bench = localStorage.getItem('codebot.showbench') === '1', filter = '';
  const list = h('div');
  const page = h('div', { class: 'page wide' },
    h('div', { class: 'pagehead' }, h('h1', null, 'Projects'),
      h('input', { type: 'text', placeholder: 'filter by name or folder', 'aria-label': 'Filter', on: { input: e => { filter = e.target.value.toLowerCase(); draw(); } } }),
      h('label', { class: 'chk', title: 'Benchmark trial projects (cap-*) are created and managed by the benchmark' }, h('input', { type: 'checkbox', checked: bench, on: { change: e => { bench = e.target.checked; localStorage.setItem('codebot.showbench', bench ? '1' : '0'); draw(); } } }), 'show benchmark trials'),
      h('span', { class: 'grow' }), h('a', { class: 'btn primary', href: '#/new' }, 'New project')),
    list);
  root.appendChild(page);
  async function draw() {
    try {
      const r = await get(`/api/projects?bench=${bench ? 1 : 0}`);
      const live = p => ['running', 'cooling', 'pause_requested', 'paused', 'crashed'].includes(p.state) ? 0 : 1;
      const ps = r.projects.filter(p => !filter || p.name.toLowerCase().includes(filter) || (p.workdir || '').toLowerCase().includes(filter))
        .sort((a, b) => live(a) - live(b) || (b.updated || 0) - (a.updated || 0));
      replace(list, ps.length ? h('div', { class: 'panel' }, h('table', { class: 't' },
        h('thead', null, h('tr', null, ['name', 'workflow', 'mode', 'state', 'progress', 'code folder', 'latest status', 'updated'].map(c => h('th', null, c)))),
        h('tbody', null, ps.map(p => h('tr', { class: 'click', tabindex: 0, on: { click: () => location.hash = `#/p/${encodeURIComponent(p.name)}`, keydown: e => { if (e.key === 'Enter') location.hash = `#/p/${encodeURIComponent(p.name)}`; } } },
          h('td', null, h('b', { class: 'mono' }, p.name)), h('td', null, p.workflow === 'shape' ? 'design' : p.workflow === 'drafts' ? 'build' : 'tasks'), h('td', null, p.mode),
          h('td', null, stateChip(p.state, p.state === 'finished' && p.drafts && p.drafts.finished ? stopText(p.drafts.finished.stop, p.drafts.examples_total) : null), p.stale_pid ? h('span', { class: 'chip', title: 'loop.pid names a process that is not a codebot loop any more (reboot or crash)' }, 'stale pid') : null),
          h('td', { class: 'small' }, progress(p)), h('td', { class: 'small path' }, p.workdir), h('td', { class: 'small' }, (p.status || '').slice(0, 120)), h('td', { class: 'small muted' }, when(p.updated)))))))
        : h('div', { class: 'empty' }, r.projects.length ? 'No project matches the filter.' : 'No projects yet. ', h('a', { href: '#/new' }, 'Create one')));
    } catch (e) { replace(list, errorBox(e)); }
  }
  return every(5000, draw);
}
