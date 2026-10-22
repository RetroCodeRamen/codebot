// Settings with their layers (shipped default / this machine / one project), Notifications, Memory.
import { h, replace, clear, when } from '../dom.js';
import { get, post } from '../api.js';
import { errorBox, toast, confirmDialog, formDialog, tabs } from '../ui.js';
import { render as md } from '../markdown.js';
import { actionLog } from './machine.js';

export function mount(root, parts, params, app) {
  const tab = parts[0] || 'settings';
  const body = h('div');
  const t = tabs([{ id: 'settings', label: 'Settings' }, { id: 'notifications', label: 'Notifications' }, { id: 'memory', label: 'Memory' }], tab, id => { location.hash = `#/settings/${id}`; });
  root.appendChild(h('div', { class: 'page wide' }, h('div', { class: 'pagehead' }, h('h1', null, 'Settings')), t.el, h('div', { style: { marginTop: '12px' } }, body)));
  if (tab === 'notifications') notificationsTab(body); else if (tab === 'memory') memoryTab(body); else settingsTab(body, params.project, params.filter);
}

const GROUP_ORDER = ['drafts', 'model', 'heat', 'sandbox', 'notifications', 'roles', 'tasks', 'legacy loop', 'expert'];

async function settingsTab(box, project, initialFilter) {
  let projects = [];
  try { projects = (await get('/api/projects')).projects.map(p => p.name); } catch (e) {}
  const pick = h('select', { on: { change: e => { location.hash = `#/settings/settings${e.target.value ? `?project=${encodeURIComponent(e.target.value)}` : ''}`; } } },
    h('option', { value: '' }, 'this machine (all projects)'), projects.map(p => h('option', { value: p, selected: p === project }, `project ${p}`)));
  const list = h('div'); let filter = (initialFilter || '').toLowerCase();
  replace(box, h('div', { class: 'row', style: { marginBottom: '10px' } }, h('span', null, 'Edit settings for'), pick,
    h('input', { type: 'text', placeholder: 'filter', value: initialFilter || '', on: { input: e => { filter = e.target.value.toLowerCase(); draw(last); } } }),
    h('span', { class: 'muted small' }, 'A project overrides this machine, which overrides the shipped defaults (config/codebot.env, never edited here).')), list);
  let last = null;
  async function load() { try { last = await get(`/api/settings${project ? `?project=${encodeURIComponent(project)}` : ''}`); draw(last); } catch (e) { replace(list, errorBox(e)); } }
  function draw(r) {
    if (!r) return; clear(list);
    const groups = {};
    for (const s of r.settings) if (!filter || s.key.toLowerCase().includes(filter) || (s.note || '').toLowerCase().includes(filter) || s.group === filter) (groups[s.group] = groups[s.group] || []).push(s);
    const rank = g => { const i = GROUP_ORDER.indexOf(g); return i < 0 ? GROUP_ORDER.length : i; };
    for (const [g, rows] of Object.entries(groups).sort((a, b) => rank(a[0]) - rank(b[0]))) {
      list.appendChild(h('div', { class: 'panel', style: { marginBottom: '12px' } }, h('div', { class: 'ph' }, h('h2', null, g)),
        h('table', { class: 't' }, h('thead', null, h('tr', null, ['setting', 'effective', 'from', 'shipped', 'this machine', project ? 'this project' : null, 'applies', ''].filter(Boolean).map(c => h('th', null, c)))),
          h('tbody', null, rows.map(s => h('tr', null,
            h('td', null, h('b', { class: 'mono' }, s.key), s.note ? h('div', { class: 'muted small' }, s.note) : null),
            h('td', { class: 'mono' }, s.effective ?? '–'), h('td', null, h('span', { class: ['chip', s.source === 'project' ? 'act' : s.source === 'machine' ? 'ok' : ''] }, s.source)),
            h('td', { class: 'mono small' }, s.layers.shipped ?? ''), h('td', { class: 'mono small' }, s.layers.machine ?? ''), project ? h('td', { class: 'mono small' }, s.layers.project ?? '') : null,
            h('td', { class: 'small muted' }, s.applies), h('td', null, editable(s) ? h('button', { type: 'button', on: { click: () => edit(s) } }, 'Change…') : h('span', { class: 'muted small', title: s.type === 'command' ? 'Commands are edited in the file by hand' : 'Written by codebot' }, s.type === 'structural' ? 'codebot' : 'file only'))))))));
    }
  }
  const editable = s => !['command', 'structural'].includes(s.type) && s.scopes.includes(project ? 'project' : 'machine');
  async function edit(s) {
    const scope = project ? 'project' : 'machine';
    const cur = scope === 'project' ? s.layers.project : s.layers.machine;
    const field = s.type === 'bool' ? { name: 'value', label: s.key, type: 'select', value: cur ?? s.effective ?? '0', options: [{ value: '0', label: '0 (off)' }, { value: '1', label: '1 (on)' }] }
      : s.type === 'choice' ? { name: 'value', label: s.key, type: 'select', value: cur ?? s.effective, options: (s.rule || []).map(o => ({ value: o, label: o })) }
      : { name: 'value', label: s.key, type: s.type === 'int' || s.type === 'float' ? 'number' : 'text', value: s.type === 'secret' ? '' : (cur ?? ''), step: s.type === 'float' ? 'any' : 1,
          hint: s.type === 'secret' ? 'Write-only: the current value is never shown.' : (Array.isArray(s.rule) && s.rule.length === 2 && typeof s.rule[0] === 'number' ? `between ${s.rule[0]} and ${s.rule[1]}` : '') };
    const v = await formDialog({ title: `${s.key} for ${project ? `project ${project}` : 'this machine'}`, submit: 'Save',
      intro: h('div', { class: 'stack' }, s.note ? h('p', null, s.note) : null, h('p', { class: 'muted small' }, `Applies: ${s.applies}. Currently ${s.effective ?? 'unset'} (from ${s.source}).`),
        cur != null ? h('button', { type: 'button', on: { click: async () => { document.getElementById('dialog').close(); await save(s, scope, null, true); } } }, `Remove the ${scope} override`) : null),
      fields: [field] });
    if (!v) return;
    await save(s, scope, v.value, false);
  }
  async function save(s, scope, value, reset) {
    let confirm = false;
    if (!reset && (s.key === 'PHONE_CONTROL' || s.key === 'NTFY_ENABLED') && String(value) === '1') {
      confirm = await confirmDialog({ title: s.key === 'PHONE_CONTROL' ? 'Turn phone control on?' : 'Turn notifications on?', confirm: 'Turn on', danger: s.key === 'PHONE_CONTROL',
        body: h('p', null, s.key === 'PHONE_CONTROL' ? 'Anyone who knows the ntfy topic can then pause, resume and stop runs and answer questions. Keep the topic secret.' : 'codebot will send messages to the ntfy server, off this machine.') });
      if (!confirm) return;
    }
    try { last = (await post('/api/settings', { key: s.key, scope, project, value, reset, confirm })).settings ? await get(`/api/settings${project ? `?project=${encodeURIComponent(project)}` : ''}`) : last; draw(last); toast(reset ? `${s.key}: override removed` : `${s.key} saved; applies ${s.applies}`); }
    catch (e) { toast(e.message, 'bad'); }
  }
  load();
}

async function notificationsTab(box) {
  try {
    const r = await get('/api/notifications'); const by = Object.fromEntries(r.settings.map(s => [s.key, s]));
    const v = k => (by[k] || {}).effective;
    replace(box, h('div', { class: 'grid2' },
      h('div', { class: 'panel' }, h('div', { class: 'ph' }, h('h2', null, 'Push notifications (ntfy)')), h('div', { class: 'pb stack' },
        h('dl', { class: 'kv' }, h('dt', null, 'enabled'), h('dd', null, v('NTFY_ENABLED') === '1' ? h('span', { class: 'chip ok' }, 'on') : h('span', { class: 'chip' }, 'off')),
          h('dt', null, 'server'), h('dd', { class: 'mono' }, v('NTFY_SERVER') || '–'), h('dt', null, 'topic'), h('dd', null, v('NTFY_TOPIC') ? 'set (hidden: it works like a password)' : h('span', { class: 'stale' }, 'not set')),
          h('dt', null, 'token'), h('dd', null, v('NTFY_TOKEN') ? 'set (hidden)' : 'none'), h('dt', null, 'each task'), h('dd', null, v('NOTIFY_EACH_TASK') === '1' ? 'on' : 'off')),
        h('div', { class: 'row' }, h('a', { class: 'btn', href: '#/settings/settings?filter=notifications' }, 'Change in Settings'),
          h('button', { type: 'button', on: { click: async () => { if (!await confirmDialog({ title: 'Send a test notification?', confirm: 'Send', body: h('p', null, 'One message goes to the ntfy server, off this machine.') })) return;
            try { const x = await post('/api/notifications/test', { confirm: true }); toast(x.ok ? 'sent' : (x.out || 'failed'), x.ok ? '' : 'bad'); } catch (e) { toast(e.message, 'bad'); } } } }, 'Send a test')))),
      h('div', { class: 'panel' }, h('div', { class: 'ph' }, h('h2', null, 'Phone control')), h('div', { class: 'pb stack' },
        v('PHONE_CONTROL') === '1' ? h('div', { class: 'note warn' }, 'On: anyone with the topic can publish pause / resume / stop / status or an answer to <topic>-reply.') : h('div', { class: 'note' }, 'Off: runs only listen to this machine.'),
        h('p', { class: 'muted small' }, 'Turn it on or off in Settings (PHONE_CONTROL); turning it on asks for confirmation.')))),
      h('div', { class: 'panel', style: { marginTop: '14px' } }, h('div', { class: 'ph' }, h('h2', null, 'What left this machine'), h('span', { class: 'muted small' }, 'logs/egress.jsonl: every message the harness sent (destination host and purpose; never the content)')),
        r.egress.length ? h('table', { class: 't' }, h('thead', null, h('tr', null, ['when', 'project', 'purpose', 'destination', 'result'].map(c => h('th', null, c)))), h('tbody', null, runs(r.egress.slice().reverse()).map(g => h('tr', null,
          h('td', { class: 'small mono' }, g.n > 1 ? `${when(g.last.ts)} – ${when(g.first.ts)}` : when(g.first.ts)), h('td', { class: 'mono' }, g.first.project || ''), h('td', null, g.first.purpose, g.n > 1 ? h('span', { class: 'muted' }, ` ×${g.n}`) : null), h('td', { class: 'mono' }, g.first.dest), h('td', null, g.first.ok ? 'ok' : h('span', { class: 'stale' }, 'failed'))))))
          : h('div', { class: 'pb muted' }, 'Nothing has been sent.')));
  } catch (e) { replace(box, errorBox(e)); }
}

async function memoryTab(box) {
  try {
    const r = await get('/api/memory');
    const ta = h('textarea', { rows: 12 }, r.expertise || '');
    replace(box, h('div', { class: 'note' }, 'Memory belongs to the task pipeline (legacy): your preferences go into its prompts, and lessons it learned are kept only while the tasks that use them pass. Drafts mode uses neither.'),
      h('div', { class: 'grid2' },
        h('div', { class: 'panel' }, h('div', { class: 'ph' }, h('h2', null, 'Your preferences (memory/expertise.md)')), h('div', { class: 'pb stack' }, ta,
          h('div', { class: 'row' }, h('button', { type: 'button', class: 'primary', on: { click: async () => { try { await post('/api/memory', { expertise: ta.value }); toast('saved'); } catch (e) { toast(e.message, 'bad'); } } } }, 'Save')))),
        h('div', { class: 'panel' }, h('div', { class: 'ph' }, h('h2', null, 'Lessons (generated)'), h('span', { class: 'spacer' }),
          h('button', { type: 'button', title: 'Prune lessons that go with failure and regenerate lessons.md', on: { click: async () => { try { const x = await post('/api/memory/tidy', {}); toast('tidy started'); box.appendChild(actionLog(x.action)); } catch (e) { toast(e.message, 'bad'); } } } }, 'Tidy')),
          r.lessons && r.lessons.trim() ? md(r.lessons) : h('div', { class: 'pb muted' }, 'No lessons recorded.'))));
  } catch (e) { replace(box, errorBox(e)); }
}

// consecutive identical messages (the phone poll every 20 s) as one row with a count; newest first
function runs(rows) {
  const out = [];
  for (const e of rows) {
    const g = out[out.length - 1], k = [e.project, e.purpose, e.dest, !!e.ok].join('|');
    if (g && g.k === k) { g.n++; g.last = e; } else out.push({ k, n: 1, first: e, last: e });
  }
  return out;
}
