import { h, replace, bytes, when } from '../dom.js';
import { get } from '../api.js';
import { every, errorBox } from '../ui.js';
import { stateChip, stopText } from '../components.js';

const ACTIVE = ['running', 'cooling', 'pause_requested', 'paused', 'crashed'];

export function mount(root, parts, params, app) {
  const page = h('div', { class: 'page' }); root.appendChild(page);
  const draw = async () => {
    try {
      const [ps, inbox] = await Promise.all([get('/api/projects'), get('/api/inbox')]);
      const projects = ps.projects; const active = projects.filter(p => ACTIVE.includes(p.state));
      const waiting = projects.filter(p => (p.workflow === 'shape' && (p.shape || {}).phase === 'questions' && !(p.shape || {}).busy) || p.blocked);
      const recent = projects.filter(p => !ACTIVE.includes(p.state)).sort((a, b) => (b.updated || 0) - (a.updated || 0)).slice(0, 8);
      const s = app.status || {};
      replace(page,
        h('div', { class: 'pagehead' }, h('h1', null, 'Home'), h('span', { class: 'grow' }), h('a', { class: 'btn primary', href: '#/new' }, 'New project')),
        projects.length === 0 ? h('div', { class: 'panel' }, h('div', { class: 'pb' }, h('h2', null, 'No projects yet'),
          h('p', null, 'Start with what you want, in your own words. You answer a few questions, read a short plan, and then build.'),
          h('a', { class: 'btn primary', href: '#/new' }, 'Create the first project'))) : null,
        h('div', { class: 'grid2' },
          h('div', { class: 'panel' }, h('div', { class: 'ph' }, h('h2', null, 'Running and paused')), h('div', { class: 'pb' },
            active.length ? h('table', { class: 't' }, h('tbody', null, active.map(p => row(p)))) : h('div', { class: 'muted' }, 'Nothing is running.'),
            s.job && !(s.job.kind === 'project') ? h('p', null, 'The model is in use by ', h('a', { href: s.job.kind === 'benchmark' ? `#/bench/${encodeURIComponent(s.job.name)}` : '#/machine' }, `${s.job.kind} ${s.job.name || ''}`), '.') : null)),
          h('div', { class: 'panel' }, h('div', { class: 'ph' }, h('h2', null, 'Needs your attention'), h('span', { class: 'spacer' }), h('a', { href: '#/inbox' }, 'Inbox')), h('div', { class: 'pb' },
            waiting.length ? h('ul', null, waiting.slice(0, 8).map(p => h('li', null, h('a', { href: `#/p/${encodeURIComponent(p.name)}` }, p.name), p.workflow === 'shape' ? ': questions for you' : ': a question is waiting')))
              : h('div', { class: 'muted' }, 'Nothing waiting.')),
            inbox.items.length ? h('p', { class: 'small' }, h('a', { href: '#/inbox' }, 'Inbox'), ` has ${inbox.items.length} finished ${inbox.items.length === 1 ? 'note' : 'notes'}.`) : null)),
        h('div', { class: 'panel', style: { marginTop: '14px' } }, h('div', { class: 'ph' }, h('h2', null, 'Recent projects'), h('span', { class: 'spacer' }), h('a', { href: '#/projects' }, 'All projects')),
          h('div', { class: 'pb' }, recent.length ? h('table', { class: 't' }, h('tbody', null, recent.map(p => row(p)))) : h('div', { class: 'muted' }, 'None.'))));
    } catch (e) { replace(page, errorBox(e)); }
  };
  const row = p => h('tr', { class: 'click', on: { click: () => location.hash = `#/p/${encodeURIComponent(p.name)}` } },
    h('td', null, h('b', { class: 'mono' }, p.name)), h('td', null, stateChip(p.state, p.state === 'finished' && p.drafts && p.drafts.finished ? stopText(p.drafts.finished.stop, p.drafts.examples_total) : null)),
    h('td', { class: 'small' }, progress(p)), h('td', { class: 'small muted' }, when(p.updated)));
  return every(5000, draw);
}

export function progress(p) {
  if (p.workflow === 'shape') {
    const s = p.shape || {};
    if (s.busy) return 'Reading it now';
    if (s.phase === 'questions') return 'Questions for you';
    if (s.phase === 'ready') return 'Ready to build';
    return 'Working out the shape';
  }
  if (p.workflow === 'drafts') {
    const d = p.drafts || {};
    if (!d.started) return 'not started';
    return `round ${d.round} · draft ${d.draft} · ${d.best ? `best ${d.best.examples}/${d.examples_total}` : 'nothing builds yet'} · ${d.used_minutes}/${d.budget_minutes ?? '?'} min`;
  }
  const t = p.tasks || {};
  return t.frozen ? Object.entries(t.counts || {}).map(([k, v]) => `${v} ${k}`).join(', ') : (t.plan ? 'plan not frozen' : 'no plan');
}
