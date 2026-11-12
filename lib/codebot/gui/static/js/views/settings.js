// Purpose-built Settings panes (General / Notifications / Computer) plus Advanced raw table and Memory.
import { h, replace, clear, when } from '../dom.js';
import { get, post } from '../api.js';
import { errorBox, toast, confirmDialog, formDialog, tabs } from '../ui.js';
import { render as md } from '../markdown.js';
import { actionLog } from './machine.js';

const PANES = [
  { id: 'general', label: 'General' },
  { id: 'notifications', label: 'Notifications' },
  { id: 'computer', label: 'Computer' },
  { id: 'advanced', label: 'Advanced' },
  { id: 'memory', label: 'Memory' },
];

const CATALOG = {
  general: [
    { key: 'CODEBOT_MODEL', label: 'Default model', help: 'Which local model builds use unless a project overrides it.' },
    { key: 'DRAFTS_MINUTES', label: 'Default time budget (minutes)', help: 'How long a build may run before it stops, counting model and judging time.' },
    { key: 'DRAFTS_SEED', label: 'Default seed', help: 'Starting seed for new builds. Another seed gives another run of the same spec.' },
  ],
  notifications: [
    { key: 'NTFY_ENABLED', label: 'Send push notifications', help: 'When on, finished runs and questions can leave this machine via ntfy.' },
    { key: 'NTFY_SERVER', label: 'Notification server', help: 'ntfy server URL.' },
    { key: 'NTFY_TOPIC', label: 'Topic (like a password)', help: 'Anyone with this topic can see your notifications. Write-only here.' },
    { key: 'NTFY_TOKEN', label: 'Access token', help: 'Optional token for a private ntfy server. Write-only.' },
    { key: 'PHONE_CONTROL', label: 'Allow phone control', help: 'When on, anyone with the topic can pause, resume, stop, and answer from their phone.' },
    { key: 'NOTIFY_EACH_TASK', label: 'Notify on each task (legacy)', help: 'Only for the old task pipeline.' },
  ],
  computer: [
    { key: 'THERMAL_GUARD', label: 'Pause when the CPU is hot', help: 'Waits for the laptop to cool before the next model call.' },
    { key: 'THERMAL_MAX', label: 'Pause at (°C)', help: 'Model calls wait at or above this temperature.' },
    { key: 'THERMAL_RESUME', label: 'Resume at (°C)', help: 'Continue when the CPU is back at or below this.' },
    { key: 'THERMAL_MAX_WAIT', label: 'Longest wait (seconds)', help: 'Cap on how long one cooling wait may last.' },
    { key: 'SANDBOX', label: 'Sandbox for model-written code', help: 'docker = always container; auto = container when available; none = run on the host (less safe).' },
    { key: 'SANDBOX_MEMORY', label: 'Sandbox memory limit', help: 'Memory cap for the container (for example 2g).' },
  ],
};

const GROUP_ORDER = ['drafts', 'model', 'heat', 'sandbox', 'notifications', 'roles', 'tasks', 'legacy loop', 'expert'];
const HUMAN_KEYS = new Set(Object.values(CATALOG).flat().map(c => c.key));

export function mount(root, parts, params, app) {
  const tab = parts[0] && PANES.some(p => p.id === parts[0]) ? parts[0] : 'general';
  const body = h('div');
  const t = tabs(PANES, tab, id => { location.hash = `#/settings/${id}${params.project ? `?project=${encodeURIComponent(params.project)}` : ''}`; });
  root.appendChild(h('div', { class: 'page' },
    h('div', { class: 'pagehead' }, h('h1', null, 'Settings')),
    h('p', { class: 'muted', style: { marginTop: '-8px', marginBottom: '14px' } },
      'Everyday options first. Advanced shows every raw setting if you need it.'),
    t.el, h('div', { class: 'settings-body', style: { marginTop: '16px' } }, body)));
  if (tab === 'memory') memoryTab(body);
  else if (tab === 'advanced') advancedTab(body, params.project, params.filter);
  else humanPane(body, tab, params.project);
}

async function humanPane(box, pane, project) {
  let projects = [];
  try { projects = (await get('/api/projects')).projects.map(p => ({ name: p.name, title: p.title || p.name })); } catch (e) {}
  const pick = h('select', { on: { change: e => {
    const q = e.target.value ? `?project=${encodeURIComponent(e.target.value)}` : '';
    location.hash = `#/settings/${pane}${q}`;
  } } },
    h('option', { value: '' }, 'This computer (all projects)'),
    projects.map(p => h('option', { value: p.name, selected: p.name === project }, p.title === p.name ? p.name : `${p.title} (${p.name})`)));
  const form = h('div', { class: 'settings-form' });
  replace(box,
    h('div', { class: 'row', style: { marginBottom: '14px' } },
      h('label', { class: 'f', style: { flex: 1 } }, h('span', null, 'Apply changes to'), pick)),
    form);
  let last = null;
  async function load() {
    try {
      last = await get(`/api/settings${project ? `?project=${encodeURIComponent(project)}` : ''}`);
      draw();
    } catch (e) { replace(form, errorBox(e)); }
  }
  function byKey() { return Object.fromEntries((last.settings || []).map(s => [s.key, s])); }
  function draw() {
    const map = byKey();
    const fields = CATALOG[pane] || [];
    clear(form);
    for (const f of fields) {
      const s = map[f.key];
      if (!s) continue;
      form.appendChild(fieldRow(f, s, project, async () => { await load(); }));
    }
    if (pane === 'notifications') {
      form.appendChild(h('div', { class: 'row', style: { marginTop: '8px' } },
        h('button', { type: 'button', on: { click: async () => {
          if (!await confirmDialog({ title: 'Send a test notification?', confirm: 'Send',
            body: h('p', null, 'One message goes to the ntfy server, off this machine.') })) return;
          try { const x = await post('/api/notifications/test', { confirm: true }); toast(x.ok ? 'Sent' : (x.out || 'Failed'), x.ok ? '' : 'bad'); }
          catch (e) { toast(e.message, 'bad'); }
        } } }, 'Send a test notification')));
      egressBlock(form);
    }
  }
  load();
}

function fieldRow(f, s, project, reload) {
  const scope = project ? 'project' : 'machine';
  const cur = scope === 'project' ? s.layers.project : s.layers.machine;
  const editable = !['command', 'structural'].includes(s.type) && s.scopes.includes(scope);
  let control;
  if (!editable) {
    control = h('div', { class: 'muted' }, s.effective ?? '–', ' ', h('span', { class: 'small' }, `(from ${s.source})`));
  } else if (s.type === 'bool') {
    control = h('label', { class: 'chk' },
      h('input', { type: 'checkbox', checked: String(s.effective) === '1', on: { change: async e => {
        await saveKey(s, scope, project, e.target.checked ? '1' : '0', false, reload);
      } } }),
      String(s.effective) === '1' ? 'On' : 'Off');
  } else if (s.type === 'choice') {
    control = h('select', { on: { change: async e => { await saveKey(s, scope, project, e.target.value, false, reload); } } },
      (s.rule || []).map(o => h('option', { value: o, selected: o === s.effective }, o)));
  } else {
    const input = h('input', {
      type: s.type === 'int' || s.type === 'float' ? 'number' : 'text',
      value: s.type === 'secret' ? '' : (cur ?? s.effective ?? ''),
      placeholder: s.type === 'secret' ? (cur != null ? 'set (hidden) — type to replace' : 'not set') : '',
      step: s.type === 'float' ? 'any' : (s.type === 'int' ? 1 : undefined),
      style: { maxWidth: '280px' },
    });
    control = h('div', { class: 'row' }, input,
      h('button', { type: 'button', class: 'primary', on: { click: async () => {
        await saveKey(s, scope, project, input.value, false, reload);
      } } }, 'Save'),
      cur != null ? h('button', { type: 'button', class: 'ghost', on: { click: async () => {
        await saveKey(s, scope, project, null, true, reload);
      } } }, 'Use default') : null);
  }
  return h('div', { class: 'settings-field' },
    h('div', { class: 'settings-label' }, h('b', null, f.label), h('p', { class: 'muted small' }, f.help),
      h('p', { class: 'muted small' }, `Applies: ${s.applies}. Currently ${s.effective ?? 'unset'} (from ${s.source}).`)),
    h('div', { class: 'settings-control' }, control));
}

async function saveKey(s, scope, project, value, reset, reload) {
  if (!reset && (s.key === 'PHONE_CONTROL' || s.key === 'NTFY_ENABLED') && String(value) === '1') {
    const ok = await confirmDialog({
      title: s.key === 'PHONE_CONTROL' ? 'Turn phone control on?' : 'Turn notifications on?',
      confirm: 'Turn on', danger: s.key === 'PHONE_CONTROL',
      body: h('p', null, s.key === 'PHONE_CONTROL'
        ? 'Anyone who knows the ntfy topic can then pause, resume and stop runs and answer questions. Keep the topic secret.'
        : 'codebot will send messages to the ntfy server, off this machine.') });
    if (!ok) { if (reload) await reload(); return; }
  }
  try {
    await post('/api/settings', { key: s.key, scope, project, value, reset, confirm: true });
    toast(reset ? `${s.key}: override removed` : 'Saved');
    if (reload) await reload();
  } catch (e) { toast(e.message, 'bad'); if (reload) await reload(); }
}

async function egressBlock(form) {
  try {
    const r = await get('/api/notifications');
    form.appendChild(h('div', { class: 'panel', style: { marginTop: '18px' } },
      h('div', { class: 'ph' }, h('h2', null, 'What left this machine')),
      r.egress.length ? h('table', { class: 't' },
        h('thead', null, h('tr', null, ['when', 'project', 'purpose', 'destination', 'result'].map(c => h('th', null, c)))),
        h('tbody', null, runs(r.egress.slice().reverse()).map(g => h('tr', null,
          h('td', { class: 'small mono' }, g.n > 1 ? `${when(g.last.ts)} – ${when(g.first.ts)}` : when(g.first.ts)),
          h('td', null, g.first.project || ''), h('td', null, g.first.purpose, g.n > 1 ? h('span', { class: 'muted' }, ` ×${g.n}`) : null),
          h('td', { class: 'mono' }, g.first.dest), h('td', null, g.first.ok ? 'ok' : h('span', { class: 'stale' }, 'failed'))))))
        : h('div', { class: 'pb muted' }, 'Nothing has been sent.')));
  } catch (e) { /* optional */ }
}

async function advancedTab(box, project, initialFilter) {
  let projects = [];
  try { projects = (await get('/api/projects')).projects.map(p => p.name); } catch (e) {}
  const pick = h('select', { on: { change: e => {
    location.hash = `#/settings/advanced${e.target.value ? `?project=${encodeURIComponent(e.target.value)}` : ''}`;
  } } },
    h('option', { value: '' }, 'This computer'), projects.map(p => h('option', { value: p, selected: p === project }, p)));
  const list = h('div'); let filter = (initialFilter || '').toLowerCase();
  replace(box, h('div', { class: 'note' }, 'Raw environment keys. Prefer General, Notifications, and Computer when you can.'),
    h('div', { class: 'row', style: { marginBottom: '10px' } }, h('span', null, 'Scope'), pick,
      h('input', { type: 'text', placeholder: 'Filter keys', value: initialFilter || '', on: { input: e => { filter = e.target.value.toLowerCase(); draw(last); } } })),
    list);
  let last = null;
  async function load() {
    try { last = await get(`/api/settings${project ? `?project=${encodeURIComponent(project)}` : ''}`); draw(last); }
    catch (e) { replace(list, errorBox(e)); }
  }
  function draw(r) {
    if (!r) return; clear(list);
    const groups = {};
    for (const s of r.settings) {
      if (HUMAN_KEYS.has(s.key) && !filter) continue; // keep panes uncluttered unless searching
      if (!filter || s.key.toLowerCase().includes(filter) || (s.note || '').toLowerCase().includes(filter) || s.group === filter)
        (groups[s.group] = groups[s.group] || []).push(s);
    }
    const rank = g => { const i = GROUP_ORDER.indexOf(g); return i < 0 ? GROUP_ORDER.length : i; };
    for (const [g, rows] of Object.entries(groups).sort((a, b) => rank(a[0]) - rank(b[0]))) {
      list.appendChild(h('div', { class: 'panel', style: { marginBottom: '12px' } }, h('div', { class: 'ph' }, h('h2', null, g)),
        h('table', { class: 't' }, h('thead', null, h('tr', null, ['setting', 'effective', 'from', 'applies', ''].map(c => h('th', null, c)))),
          h('tbody', null, rows.map(s => h('tr', null,
            h('td', null, h('b', { class: 'mono' }, s.key), s.note ? h('div', { class: 'muted small' }, s.note) : null),
            h('td', { class: 'mono' }, s.effective ?? '–'),
            h('td', null, h('span', { class: ['chip', s.source === 'project' ? 'act' : s.source === 'machine' ? 'ok' : ''] }, s.source)),
            h('td', { class: 'small muted' }, s.applies),
            h('td', null, editable(s, project) ? h('button', { type: 'button', on: { click: () => editAdv(s, project, load) } }, 'Change…')
              : h('span', { class: 'muted small' }, s.type === 'structural' ? 'codebot' : 'file only'))))))));
    }
    if (!list.children.length) list.appendChild(h('div', { class: 'muted' }, 'No matching advanced settings. Clear the filter to see power-user keys.'));
  }
  load();
}

function editable(s, project) {
  return !['command', 'structural'].includes(s.type) && s.scopes.includes(project ? 'project' : 'machine');
}

async function editAdv(s, project, reload) {
  const scope = project ? 'project' : 'machine';
  const cur = scope === 'project' ? s.layers.project : s.layers.machine;
  const field = s.type === 'bool'
    ? { name: 'value', label: s.key, type: 'select', value: cur ?? s.effective ?? '0', options: [{ value: '0', label: '0 (off)' }, { value: '1', label: '1 (on)' }] }
    : s.type === 'choice'
      ? { name: 'value', label: s.key, type: 'select', value: cur ?? s.effective, options: (s.rule || []).map(o => ({ value: o, label: o })) }
      : { name: 'value', label: s.key, type: s.type === 'int' || s.type === 'float' ? 'number' : 'text',
          value: s.type === 'secret' ? '' : (cur ?? ''), step: s.type === 'float' ? 'any' : 1,
          hint: s.type === 'secret' ? 'Write-only: the current value is never shown.' : '' };
  const v = await formDialog({
    title: `${s.key}`, submit: 'Save',
    intro: h('div', { class: 'stack' }, s.note ? h('p', null, s.note) : null,
      h('p', { class: 'muted small' }, `Applies: ${s.applies}. Currently ${s.effective ?? 'unset'} (from ${s.source}).`),
      cur != null ? h('button', { type: 'button', on: { click: async () => {
        document.getElementById('dialog').close(); await saveKey(s, scope, project, null, true, reload);
      } } }, `Remove the ${scope} override`) : null),
    fields: [field],
  });
  if (!v) return;
  await saveKey(s, scope, project, v.value, false, reload);
}

async function memoryTab(box) {
  try {
    const r = await get('/api/memory');
    const ta = h('textarea', { rows: 12 }, r.expertise || '');
    replace(box, h('div', { class: 'note' },
      'Memory belongs to the legacy task pipeline. Drafts builds do not use it.'),
      h('div', { class: 'grid2' },
        h('div', { class: 'panel' }, h('div', { class: 'ph' }, h('h2', null, 'Your preferences')),
          h('div', { class: 'pb stack' }, ta,
            h('div', { class: 'row' }, h('button', { type: 'button', class: 'primary', on: { click: async () => {
              try { await post('/api/memory', { expertise: ta.value }); toast('Saved'); } catch (e) { toast(e.message, 'bad'); }
            } } }, 'Save')))),
        h('div', { class: 'panel' }, h('div', { class: 'ph' }, h('h2', null, 'Lessons'), h('span', { class: 'spacer' }),
          h('button', { type: 'button', on: { click: async () => {
            try { const x = await post('/api/memory/tidy', {}); toast('Tidy started'); box.appendChild(actionLog(x.action)); }
            catch (e) { toast(e.message, 'bad'); }
          } } }, 'Tidy')),
          r.lessons && r.lessons.trim() ? md(r.lessons) : h('div', { class: 'pb muted' }, 'No lessons recorded.'))));
  } catch (e) { replace(box, errorBox(e)); }
}

function runs(rows) {
  const out = [];
  for (const e of rows) {
    const g = out[out.length - 1], k = [e.project, e.purpose, e.dest, !!e.ok].join('|');
    if (g && g.k === k) { g.n++; g.last = e; } else out.push({ k, n: 1, first: e, last: e });
  }
  return out;
}
