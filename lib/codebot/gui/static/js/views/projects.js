import { h, replace, when } from '../dom.js';
import { get, post } from '../api.js';
import { every, errorBox, confirmDialog, toast } from '../ui.js';
import { stateChip, stopText } from '../components.js';
import { progress } from './home.js';

const ALIVE = ['running', 'cooling', 'pause_requested', 'paused'];

export function mount(root, parts, params, app) {
  let bench = localStorage.getItem('codebot.showbench') === '1', filter = '';
  const list = h('div');
  const page = h('div', { class: 'page' },
    h('div', { class: 'pagehead' },
      h('h1', null, 'Projects'),
      h('span', { class: 'grow' }),
      h('a', { class: 'btn primary', href: '#/new' }, 'New project')),
    h('p', { class: 'muted library-lead' }, 'Each card is a build. Open it to answer questions, watch progress, or delete it.'),
    h('div', { class: 'row library-toolbar' },
      h('input', { type: 'search', placeholder: 'Search by name or folder', 'aria-label': 'Search projects', style: { flex: '1', maxWidth: '320px' },
        on: { input: e => { filter = e.target.value.toLowerCase(); draw(); } } }),
      h('label', { class: 'chk', title: 'Benchmark trial projects (cap-*) are created and managed by the benchmark' },
        h('input', { type: 'checkbox', checked: bench, on: { change: e => { bench = e.target.checked; localStorage.setItem('codebot.showbench', bench ? '1' : '0'); draw(); } } }),
        'Show benchmark trials')),
    list);
  root.appendChild(page);

  async function draw() {
    try {
      const r = await get(`/api/projects?bench=${bench ? 1 : 0}`);
      const live = p => ALIVE.includes(p.state) || p.state === 'crashed' ? 0 : 1;
      const needs = p => (p.attention && p.attention.length) ? 0 : 1;
      const ps = r.projects
        .filter(p => !filter || [p.name, p.title, p.blurb, p.workdir].join(' ').toLowerCase().includes(filter))
        .sort((a, b) => needs(a) - needs(b) || live(a) - live(b) || (b.updated || 0) - (a.updated || 0));
      if (!ps.length) {
        replace(list, h('div', { class: 'empty' },
          h('h2', null, r.projects.length ? 'No matches' : 'No projects yet'),
          h('p', null, r.projects.length ? 'Try another search.' : 'Describe what you want in your own words. You answer a few questions, read a short plan, then build.'),
          r.projects.length ? null : h('div', { class: 'row', style: { justifyContent: 'center', marginTop: '16px' } },
            h('a', { class: 'btn primary', href: '#/new' }, 'New project'))));
        return;
      }
      replace(list, h('div', { class: 'proj-grid' }, ps.map(p => card(p))));
    } catch (e) { replace(list, errorBox(e)); }
  }

  function card(p) {
    const title = p.title || p.name;
    const href = `#/p/${encodeURIComponent(p.name)}`;
    const att = (p.attention || [])[0];
    const chipExtra = p.state === 'finished' && p.drafts && p.drafts.finished
      ? stopText(p.drafts.finished.stop, p.drafts.examples_total) : null;
    return h('article', { class: ['proj-card', att ? 'has-attention' : ''] },
      h('a', { class: 'proj-card-main', href },
        att ? h('div', { class: ['attn-badge', att.kind] }, labelAttention(att)) : null,
        h('h2', null, title),
        title !== p.name ? h('div', { class: 'slug muted small' }, p.name) : null,
        p.blurb ? h('p', { class: 'blurb' }, p.blurb) : null,
        h('div', { class: 'proj-card-meta' },
          stateChip(p.state, chipExtra),
          h('span', { class: 'muted small' }, progress(p)),
          h('span', { class: 'muted small' }, when(p.updated)))),
      h('div', { class: 'proj-card-actions' },
        h('a', { class: 'btn', href }, 'Open'),
        h('button', { type: 'button', class: 'ghost danger-text', disabled: ALIVE.includes(p.state),
          title: ALIVE.includes(p.state) ? 'Stop the project before deleting' : 'Remove codebot’s record (keeps your code folder)',
          on: { click: e => { e.preventDefault(); e.stopPropagation(); del(p); } } }, 'Delete…')));
  }

  function labelAttention(a) {
    if (a.kind === 'question') return 'Needs your answer';
    if (a.kind === 'finished') return 'Finished';
    if (a.kind === 'crashed') return 'Interrupted';
    return a.text || a.kind;
  }

  async function del(p) {
    const title = p.title || p.name;
    const ok = await confirmDialog({
      title: `Delete “${title}”?`, danger: true, confirm: 'Delete project',
      body: h('div', { class: 'stack' },
        h('p', null, 'Removes codebot’s record for this project (logs, rounds, settings).'),
        h('p', null, p.workdir
          ? ['Your code folder stays untouched: ', h('code', null, p.workdir)]
          : 'No separate code folder was recorded.'),
        h('p', { class: 'muted small' }, `Internal name: ${p.name}`)) });
    if (!ok) return;
    try {
      await post(`/api/project/${encodeURIComponent(p.name)}/delete`, { confirm: true });
      toast(`Deleted ${title}`);
      draw();
    } catch (e) { toast(e.message, 'bad'); }
  }

  return every(5000, draw);
}
