// One project: header with state and run controls, then files | activity | inspector. Every action is a backend call; this file only shows.
import { h, replace, clear, bytes, dur, clock, when, ago, plural } from '../dom.js';
import { get, post, q } from '../api.js';
import { confirmDialog, formDialog, toast, tabs, resizer, follow, every, errorBox, loading } from '../ui.js';
import { stateChip, stopText, versionLabel, codeView, diffView, examplesTable, scoreChart } from '../components.js';
import { render as md } from '../markdown.js';

const ALIVE = ['running', 'cooling', 'paused', 'pause_requested'];

function workshop(root, parts, params, app) {
  document.body.dataset.chrome = 'workshop';
  const name = parts[0];
  const S = { data: null, rounds: [], files: null, version: null, path: null, round: null, insp: null, actTab: 'rounds', narrow: 'activity', lastUpdated: null, busy: false };
  const stops = [];

  // ---------- skeleton ----------
  const head = h('div', { class: 'projhead' }, h('h1', null, name));
  const switcher = h('div', { class: 'paneswitch' }, [['files', 'Files'], ['activity', 'Activity'], ['inspector', 'Inspector']].map(([p, lab]) => h('button', { type: 'button', dataset: { p }, on: { click: () => showPane(p) } }, lab)));
  const filesPane = h('section', { class: 'pane files', 'aria-label': 'Files' });
  const actPane = h('section', { class: 'pane activity', 'aria-label': 'Activity' });
  const inspPane = h('section', { class: 'pane inspector', 'aria-label': 'Inspector' });
  const panes = h('div', { class: 'panes' }, filesPane, resizer(filesPane, 'files', { min: 160, max: 480 }), actPane, resizer(actPane, 'activity', { min: 280, max: 1400 }), inspPane);
  root.appendChild(h('div', { class: 'ws' }, head, switcher, panes));
  if (localStorage.getItem('codebot.files.collapsed') === '1') filesPane.classList.add('collapsed');
  function showPane(p) {
    S.narrow = p; for (const [k, el] of [['files', filesPane], ['activity', actPane], ['inspector', inspPane]]) el.classList.toggle('show', k === p);
    for (const b of switcher.children) b.classList.toggle('on', b.dataset.p === p);
  }
  showPane('activity');

  // ---------- data ----------
  async function loadProject() {
    const d = await get(`/api/project/${encodeURIComponent(name)}`);
    S.data = d; renderHead(); return d;
  }
  async function loadFiles() { S.files = await get(`/api/project/${encodeURIComponent(name)}/files`); renderFiles(); }

  // ---------- header + controls (title + state + primary verbs; details on demand) ----------
  function renderHead() {
    const d = S.data, st = d.state, dr = st.drafts || {}, status = app.status || {};
    const facts = h('div', { class: 'facts' });
    const add = (k, v, title) => facts.appendChild(h('span', { title }, k ? `${k} ` : '', h('b', null, v)));
    add('', st.workflow === 'drafts' ? 'Drafts' : 'Tasks (legacy)'); if (st.mode !== 'code') add('mode', st.mode);
    if (st.workflow === 'drafts') {
      if (dr.started) {
        add('draft', `${dr.draft}`); add('round', `${dr.round}`);
        add('best', dr.best ? `${dr.best.examples}/${dr.examples_total} examples` : 'nothing builds yet', 'Spec examples the best version passes. Requirements without examples are not checked.');
        if (dr.declared_total) add('names', `${dr.best ? dr.best.declared : 0}/${dr.declared_total}`, 'Functions and exceptions the spec declares that the best version defines');
        const pend = d.budget_pending;
        add('time', `${dr.used_minutes} / ${dr.budget_minutes ?? '?'} min${pend ? ` → ${pend} at the next round` : ''}`, 'Model and judging time used / this run\'s budget');
      } else add('', 'Not started', '');
    } else {
      const t = st.tasks || {}; if (t.frozen) add('tasks', Object.entries(t.counts || {}).map(([k, v]) => `${v} ${k}`).join(', '));
      else add('', t.plan ? 'Plan written, not frozen' : 'No plan yet');
    }
    facts.appendChild(h('span', { class: 'path', title: st.workdir }, h('b', null, st.workdir)));
    const fin = dr.finished;
    const chipExtra = st.state === 'finished' && fin ? stopText(fin.stop, dr.examples_total) : st.state === 'cooling' && st.thermal.temp ? `${Math.round(st.thermal.temp)}°C` : null;
    let mem = null;
    if (st.state === 'paused') {
      const loaded = ((status.ollama || {}).loaded || []).map(x => x.name);
      mem = status.ollama && status.ollama.loaded_known ? h('span', { class: 'chip', title: 'Read from the model server' }, loaded.length ? 'Model still loaded' : 'Model memory released') : null;
    }
    const detailsOpen = head.classList.contains('details-open');
    const detailsBtn = h('button', { type: 'button', class: 'ghost', title: 'Show path, budget, and round details',
      on: { click: () => { head.classList.toggle('details-open'); renderHead(); } } }, detailsOpen ? 'Hide details' : 'Details');
    const title = st.title || name;
    const attn = (d.attention || st.attention || []).filter(a => a.kind === 'finished' || a.kind === 'crashed');
    const updates = attn.length ? h('div', { class: 'updates compact' }, attn.map(a => h('div', { class: 'row' },
      h('span', { class: ['attn-badge', a.kind] }, a.kind === 'finished' ? 'Finished' : 'Interrupted'),
      h('span', { class: 'small' }, a.text),
      a.kind === 'finished' ? h('button', { type: 'button', class: 'ghost', on: { click: async () => {
        try { await post('/api/inbox/ack', { key: a.key }); refresh(true); } catch (e) { toast(e.message, 'bad'); }
      } } }, 'Dismiss') : null))) : null;
    replace(head, h('h1', null, title), title !== name ? h('span', { class: 'muted small' }, name) : null,
      stateChip(st.state, chipExtra), mem, h('div', { class: 'ctl' }, controls(d), detailsBtn), facts, updates);
    if (detailsOpen) head.classList.add('details-open');
  }

  function jobBlock() {
    const j = (app.status || {}).job;
    if (!j) return null;
    if (j.kind === 'project' && j.name === name) return null;
    return j;
  }

  function controls(d) {
    const st = d.state, dr = st.drafts || {}, out = [], more = [], blocker = jobBlock();
    const blockTitle = blocker ? `Busy with ${blocker.kind} ${blocker.name || ''}. One job at a time.` : '';
    if (['stopped', 'not_started', 'crashed'].includes(st.state) && !(dr.finished)) {
      out.push(h('button', { type: 'button', class: 'primary', disabled: !!blocker || S.busy, title: blockTitle || 'Start in the background (keeps running if you close this window)', on: { click: () => doAct('start') } }, 'Start'));
    }
    if (st.state === 'running' || st.state === 'cooling') {
      out.push(h('button', { type: 'button', disabled: S.busy, title: 'Stop the current step and free model memory. Resume continues.', on: { click: () => doAct('pause') } }, 'Pause'));
      more.push(h('button', { type: 'button', disabled: S.busy, title: 'Let the current step finish, then pause', on: { click: () => doAct('pause-soft') } }, 'Pause after this step'));
    }
    if (st.state === 'paused' || st.state === 'pause_requested') {
      out.push(h('button', { type: 'button', class: 'primary', disabled: S.busy || (!!blocker && st.state === 'paused'), title: blocker && st.state === 'paused' ? blockTitle : 'Continue where it left off',
        on: { click: () => doAct('resume') } }, st.state === 'pause_requested' ? 'Cancel pause' : 'Resume'));
    }
    if (ALIVE.includes(st.state)) out.push(h('button', { type: 'button', class: 'danger', disabled: S.busy, on: { click: confirmStop } }, 'Stop…'));
    if (st.workflow === 'drafts' && dr.started && !dr.finished) more.push(h('button', { type: 'button', on: { click: changeBudget } }, 'Time budget…'));
    if (st.workflow === 'drafts') more.push(h('button', { type: 'button', on: { click: runAgain }, title: 'New project, same spec and code folder, another seed' }, 'Run again…'));
    if (st.workflow === 'drafts' && dr.started) more.push(h('a', { class: 'btn', href: `#/new?from=${encodeURIComponent(name)}`, title: 'The spec of a started run is locked; edit it in a new project' }, 'Edit as new…'));
    more.push(h('a', { class: 'btn', href: `#/pipeline/${encodeURIComponent(name)}`, title: 'Modular team graph, JSON, and pipeline monitor' }, 'Pipeline…'));
    if (st.workflow === 'drafts' && !ALIVE.includes(st.state)) {
      more.push(h('button', { type: 'button', title: 'Switch this project to WORKFLOW=pipeline with the solo template (keeps the SPEC)', on: { click: upgradePipeline } }, 'Upgrade to pipeline…'));
    }
    more.push(h('a', { class: 'btn', href: `#/p/${encodeURIComponent(name)}`, title: 'Back to the simple progress view' }, 'Leave workshop'));
    if (!ALIVE.includes(st.state)) more.push(h('button', { type: 'button', class: 'danger', on: { click: deleteProject } }, 'Delete…'));
    if (blocker && !ALIVE.includes(st.state)) out.unshift(h('span', { class: 'chip act', title: blockTitle }, 'Busy: ', h('a', { href: blocker.kind === 'project' ? `#/p/${encodeURIComponent(blocker.name)}` : blocker.kind === 'benchmark' ? `#/bench/${encodeURIComponent(blocker.name)}` : '#/machine' }, `${blocker.kind} ${blocker.name || ''}`)));
    if (more.length) {
      const pop = h('div', { class: 'pop', hidden: true }, more);
      const btn = h('button', { type: 'button', 'aria-haspopup': 'true', 'aria-expanded': 'false', on: { click: e => {
        e.stopPropagation();
        const willOpen = pop.hidden;
        pop.hidden = !willOpen;
        btn.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
        if (willOpen) {
          const close = () => { pop.hidden = true; btn.setAttribute('aria-expanded', 'false'); document.removeEventListener('click', close); };
          setTimeout(() => document.addEventListener('click', close), 0);
        }
      } } }, 'More');
      out.push(h('div', { class: 'menu' }, btn, pop));
    }
    return out;
  }

  async function doAct(what) {
    S.busy = true; renderHead();
    try {
      const r = await post(`/api/project/${encodeURIComponent(name)}/${what}`, {});
      toast(r.out || (r.ok ? 'done' : 'failed'), r.ok ? '' : 'bad');
    } catch (e) { toast(e.message, 'bad'); }
    S.busy = false; await refresh(true);
  }
  async function upgradePipeline() {
    const ok = await confirmDialog({
      title: 'Upgrade to pipeline?',
      confirm: 'Install solo pipeline',
      body: h('div', { class: 'stack' },
        h('p', null, 'Sets WORKFLOW=pipeline and installs the solo developer template. Your SPEC and code folder stay.'),
        h('p', { class: 'muted' }, 'You can change the team on the Pipeline page afterward. Drafts remains available for new projects.'))
    });
    if (!ok) return;
    try {
      await post(`/api/project/${encodeURIComponent(name)}/pipeline/init`, { template: 'solo', force: true });
      toast('Pipeline installed');
      location.hash = `#/pipeline/${encodeURIComponent(name)}`;
    } catch (e) { toast(e.message, 'bad'); }
  }
  async function confirmStop() {
    const ok = await confirmDialog({ title: `Stop ${name}?`, danger: true, confirm: 'Stop the run',
      body: h('div', { class: 'stack' }, h('p', null, 'Stopping ends the loop: the step that is running is killed and the model is unloaded. The best version so far stays committed.'),
        h('p', null, 'Pausing is different: the loop stays and Resume continues where it was. ', S.data.state.workflow === 'drafts' ? 'A stopped drafts run can be started again later; it keeps its rounds.' : ''),
        h('p', { class: 'muted' }, 'Equivalent command: ', h('code', null, `codebot stop ${name}`))) });
    if (!ok) return;
    S.busy = true; renderHead();
    try { const r = await post(`/api/project/${encodeURIComponent(name)}/stop`, { confirm: true }); toast(r.out || 'stopped', r.ok ? '' : 'bad'); }
    catch (e) { toast(e.message, 'bad'); }
    S.busy = false; await refresh(true);
  }
  async function changeBudget() {
    const dr = S.data.state.drafts;
    const v = await formDialog({ title: 'Time budget of this drafts run', submit: 'Set budget',
      intro: h('div', { class: 'stack' }, h('p', null, `Used so far: ${dr.used_minutes} min of ${dr.budget_minutes} min (model and judging time; pauses do not count).`),
        h('p', { class: 'muted' }, 'This sets DRAFTS_MINUTES for this project only. The engine reads it before every round, so it applies at the start of the next round. It is not the benchmark\'s per-task minutes.')),
      fields: [{ name: 'minutes', label: 'Budget (minutes)', type: 'number', value: Math.max(dr.budget_minutes || 60, Math.ceil(dr.used_minutes) + 15), min: 1, max: 1440, step: 1 }],
      validate: v => (!(Number(v.minutes) >= 1) ? 'at least 1 minute' : null) });
    if (!v) return;
    try { const r = await post(`/api/project/${encodeURIComponent(name)}/budget`, { minutes: v.minutes }); toast(`Budget set to ${r.minutes} min; ${r.applies}`); }
    catch (e) { toast(e.message, 'bad'); }
    refresh(true);
  }
  async function deleteProject() {
    const st = S.data.state, title = st.title || name;
    const ok = await confirmDialog({
      title: `Delete “${title}”?`, danger: true, confirm: 'Delete project',
      body: h('div', { class: 'stack' },
        h('p', null, 'Removes codebot’s record for this project (logs, rounds, settings).'),
        h('p', null, st.workdir ? ['Your code folder stays untouched: ', h('code', null, st.workdir)] : 'No separate code folder was recorded.')) });
    if (!ok) return;
    try {
      await post(`/api/project/${encodeURIComponent(name)}/delete`, { confirm: true });
      toast(`Deleted ${title}`);
      location.hash = '#/projects';
    } catch (e) { toast(e.message, 'bad'); }
  }
  async function runAgain() {
    const st = S.data.state, seed = (st.drafts && st.drafts.settings && Number(st.drafts.settings.seed)) || 1;
    const v = await formDialog({ title: 'Run again from the same spec', submit: 'Create project',
      intro: h('p', { class: 'muted' }, 'A finished or stopped drafts run is not restarted: this creates a new project with a copy of the spec, the same code folder (its own branch) and this run\'s drafts settings.'),
      fields: [{ name: 'name', label: 'New project name', value: nextName(name) }, { name: 'seed', label: 'Seed (another seed gives another run)', type: 'number', value: seed + 1, min: 0 },
               { name: 'start', label: 'Start it now', type: 'checkbox', value: !jobBlock() }],
      validate: v => (/^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(v.name) ? null : 'letters, digits, ".", "_" and "-", starting with a letter or digit') });
    if (!v) return;
    try {
      const r = await post(`/api/project/${encodeURIComponent(name)}/run-again`, v);
      if (!r.ok) { toast(r.out || 'not created', 'bad'); return; }
      toast(`Created ${v.name}` + (r.start ? (r.start.ok ? ' and started it' : `; start failed: ${r.start.out}`) : ''));
      location.hash = `#/p/${encodeURIComponent(v.name)}`;
    } catch (e) { toast(e.message, 'bad'); }
  }

  // ---------- files pane ----------
  function renderFiles() {
    const f = S.files, d = S.data; clear(filesPane);
    const collapse = h('button', { type: 'button', class: 'ghost', title: 'Hide the files pane', on: { click: () => { filesPane.classList.add('collapsed'); localStorage.setItem('codebot.files.collapsed', '1'); renderInspector(); } } }, '⟨');
    filesPane.appendChild(h('div', { class: 'panehead' }, h('span', { class: 'title grow' }, 'Files'), collapse));
    const body = h('div', { class: 'pane-body' }); filesPane.appendChild(body);
    if (!f) { loading(body); return; }
    const tree = h('div', { class: 'tree' });
    const node = (label, sel, onClick, cls, ic = '·', title) => h('div', { class: ['node', cls, sel ? 'on' : ''], tabindex: 0, title, on: { click: onClick, keydown: e => { if (e.key === 'Enter') onClick(); } } }, h('span', { class: 'ic' }, ic), h('span', { class: 'nm' }, label));
    tree.appendChild(h('div', { class: 'grp' }, 'Specification'));
    tree.appendChild(node('SPEC.md', S.version === 'spec', () => select('spec', 'SPEC.md'), '', '§', d.state.state !== 'not_started' ? 'Locked: the run started from this spec' : 'The spec'));
    if (f.workflow === 'drafts') {
      const v = f.versions || {};
      const opts = [];
      if (v.best_round) opts.push(['best', `best · round ${v.best_round}`]);
      if (v.latest_round && v.latest_round !== v.best_round) opts.push(['latest', `latest · round ${v.latest_round}`]);
      for (const r of (v.rounds || []).slice().reverse()) opts.push([`round:${r}`, `round ${r}${r === v.best_round ? ' (best)' : ''}`]);
      if (v.has_baseline) opts.push(['baseline', 'baseline (at start)']);
      if (v.has_snapshot) opts.push(['snapshot', 'working snapshot']);
      const codeVer = S.version && S.version !== 'spec' && S.version !== 'harness' && S.version !== 'delivered' ? S.version : (v.best_round ? 'best' : v.latest_round ? 'latest' : null);
      if (opts.length) {
        const sel = h('select', { 'aria-label': 'Which version of the code to list', on: { change: e => { S.version = e.target.value; S.path = null; S.round = S.version.startsWith('round:') ? +S.version.slice(6) : null; renderFiles(); openDefaultFile(); } } },
          opts.map(([val, lab]) => h('option', { value: val, selected: val === codeVer }, lab)));
        tree.appendChild(h('div', { class: 'versionbar' }, h('span', { class: 'vl' }, 'Code version'), sel));
        const files = codeVer === 'best' ? groupFiles(f, 'best') : codeVer === 'latest' ? groupFiles(f, 'latest') : null;
        if (files) for (const x of files) tree.appendChild(node(x.path, S.version === codeVer && S.path === x.path, () => select(codeVer, x.path), '', '▫'));
        else if (codeVer) { tree.appendChild(h('div', { class: 'muted small', style: { padding: '2px 10px' } }, 'files of this version open in the inspector')); lazyList(tree, codeVer, node); }
      } else tree.appendChild(h('div', { class: 'muted small', style: { padding: '4px 10px' } }, 'no code yet'));
      if (v.delivered_commit) tree.appendChild(node(`delivered ${v.delivered_commit.slice(0, 7)}`, S.insp === 'delivered', () => { S.insp = 'delivered'; renderInspector(); }, '', '⎇', 'The commit on branch codebot/' + name));
    }
    tree.appendChild(h('div', { class: 'grp' }, 'Harness state and logs'));
    for (const x of f.harness || []) tree.appendChild(node(x.path, S.version === 'harness' && S.path === x.path, () => select('harness', x.path), 'harness', '·', bytes(x.size)));
    body.appendChild(tree);
  }
  const groupFiles = (f, ver) => ((f.groups || []).find(g => g.version === ver) || {}).files || [];
  async function lazyList(tree, ver, node) {
    if (!ver.startsWith('round:')) return;
    try { const r = await get(`/api/project/${encodeURIComponent(name)}/round/${ver.slice(6)}`); for (const x of r.files) tree.appendChild(node(x.path, S.path === x.path && S.version === ver, () => select(ver, x.path), '', '▫')); }
    catch (e) { tree.appendChild(errorBox(e)); }
  }
  function select(version, path) { S.version = version; S.path = path; S.insp = 'file'; if (S.narrow !== 'inspector' && window.innerWidth < 1100) showPane('inspector'); renderFiles(); renderInspector(); }
  function openDefaultFile() {
    const f = S.files; if (!f) return;
    if (S.version && S.version.startsWith('round:')) { S.insp = 'round'; renderInspector(); return; }
    const files = groupFiles(f, S.version); if (files.length) select(S.version, files[0].path);
  }

  // ---------- activity pane ----------
  const actBody = h('div', { class: 'pane-body' });
  const actTabs = tabs([{ id: 'rounds', label: S.data && S.data.state.workflow === 'tasks' ? 'Tasks' : 'Rounds' }, { id: 'log', label: 'Live log' }, { id: 'report', label: 'Report' }, { id: 'audit', label: 'Audit' }],
    'rounds', id => { S.actTab = id; renderActivity(true); });
  actPane.append(h('div', { class: 'panehead' }, h('span', { class: 'title' }, 'Activity'), h('span', { class: 'grow' }),
    h('button', { type: 'button', class: 'ghost', title: 'Show the files pane', hidden: false, on: { click: () => { filesPane.classList.remove('collapsed'); localStorage.setItem('codebot.files.collapsed', '0'); } } }, 'Files ⟩')),
    actTabs.el, actBody);
  const fol = follow(actBody);
  let logOffset = -1, logEl = null;

  function renderActivity(reset) {
    if (reset) { clear(actBody); logEl = null; logOffset = -1; }
    if (S.actTab === 'rounds') return S.data && S.data.state.workflow === 'tasks' ? renderTasks() : renderJournal();
    if (S.actTab === 'log') return renderLog();
    if (S.actTab === 'report') return renderText('report');
    if (S.actTab === 'audit') return renderText('audit');
  }

  function roundOutcome(r) {
    const bits = [];
    if (r.code === false) bits.push(h('span', { class: 'chip bad' }, 'no code in reply'));
    else if (r.built === false) bits.push(h('span', { class: 'chip bad' }, 'did not build'));
    else if (r.built) bits.push(h('span', { class: 'chip ok' }, `${r.examples} examples pass`));
    if (r.declared) bits.push(h('span', { class: 'chip' }, `${r.declared} names`));
    if (r.done_reason === 'length') bits.push(h('span', { class: 'chip act', title: 'The reply hit its output cap and was cut off' }, 'cut off'));
    if (r.done_reason === 'loop') bits.push(h('span', { class: 'chip act', title: 'Stopped while streaming: it was repeating the same lines' }, 'repetitive, stopped'));
    if (r.unchanged) bits.push(h('span', { class: 'chip', title: 'The repair returned the same code' }, 'unchanged repair'));
    return bits;
  }

  function renderJournal() {
    const rows = S.rounds, d = S.data; if (!d) return;
    clear(actBody);
    const dr = d.state.drafts || {};
    if (!rows.length && !d.state.step) { actBody.appendChild(h('div', { class: 'empty' }, d.state.state === 'not_started' ? 'Not started yet. Start runs the first round.' : 'No rounds recorded yet.')); }
    if (rows.length) actBody.appendChild(scoreChart(rows, dr.examples_total, { onPick: n => pickRound(n) }));
    let best = -1;
    rows.forEach((r, i) => {
      const score = r.built ? (r.examples || 0) + (r.declared || 0) : -1, newBest = r.built && score > best; if (newBest) best = score;
      const isBest = dr.best && dr.best.round === r.round;
      const sel = S.round === r.round;
      const det = h('div', { class: 'det', hidden: !sel },
        h('dl', { class: 'kv small' },
          h('dt', null, 'duration'), h('dd', null, dur(r.secs) + (r.thermal_wait ? ` (+ ${dur(r.thermal_wait)} cooling first)` : '')),
          h('dt', null, 'tokens'), h('dd', null, `${r.prompt_tokens || '–'} in, ${r.gen_tokens ?? '–'} out of a ${r.num_predict ?? '–'} cap`),
          h('dt', null, 'reply ended'), h('dd', null, { stop: 'on its own', length: 'cut off at the cap', loop: 'stopped as repetitive' }[r.done_reason] || r.done_reason || '–'),
          h('dt', null, 'files'), h('dd', null, (r.files || []).join(', ') || '–'),
          r.used_s != null ? [h('dt', null, 'run time after'), h('dd', null, dur(r.used_s))] : null),
        h('div', { class: 'row' }, h('button', { type: 'button', on: { click: e => { e.stopPropagation(); pickRound(r.round); } } }, 'Open round in inspector')));
      const ent = h('div', { class: ['jr', sel ? 'sel' : '', r.fresh && i > 0 ? 'draftstart' : ''] },
        h('div', { class: 'jh', on: { click: () => { S.round = S.round === r.round ? null : r.round; det.hidden = S.round !== r.round; ent.classList.toggle('sel', S.round === r.round); if (S.round) pickRound(r.round, false); } } },
          h('div', { class: 'rn' }, h('b', null, `R${r.round}`), h('br'), `draft ${r.draft}`),
          h('div', { class: 'what' }, h('div', { class: 'l1' }, h('span', null, r.fresh ? 'fresh draft' : 'repair'), roundOutcome(r), isBest ? h('span', { class: 'chip ok', title: 'The version delivered now' }, 'best version') : newBest ? h('span', { class: 'chip' }, 'new best then') : null),
            r.restart_next ? h('div', { class: 'l2' }, '→ next round starts a fresh draft') : null),
          h('div', { class: 'muted small mono' }, dur(r.secs))),
        det);
      actBody.appendChild(ent);
    });
    const ph = d.phase;
    if (ph) {
      const since = ph.since ? (typeof ph.since === 'number' ? ph.since : Date.parse(ph.since) / 1000) : null;
      const what = { generating: 'generating: the model is writing the reply', judging: 'judging: building and running the examples', cooling: `cooling: waiting for the CPU to cool${ph.temp ? ` (${Math.round(ph.temp)}°C)` : ''}` }[ph.phase];
      actBody.appendChild(h('div', { class: 'jr live' }, h('div', { class: 'jh' }, h('div', { class: 'rn' }, h('b', null, ph.round ? `R${ph.round}` : 'now')),
        h('div', { class: 'what' }, h('div', { class: 'l1' }, h('span', { class: 'chip act pulse' }, ph.phase), h('span', null, what))),
        h('div', { class: 'muted small mono' }, since ? `${clock(since)} · ${dur(Date.now() / 1000 - since)}` : ''))));
    } else if (d.state.state === 'pause_requested') actBody.appendChild(h('div', { class: 'note warn' }, 'Pause requested: the loop pauses when the round that is running finishes.'));
    else if (d.state.state === 'finished' && dr.finished) actBody.appendChild(h('div', { class: ['note', dr.finished.stop === 'examples pass' ? 'ok' : 'warn'] }, `Finished: ${stopText(dr.finished.stop, dr.examples_total)} after ${dr.finished.minutes} min (${when(dr.finished.at)}). `, dr.best ? `The best version (round ${dr.best.round}) passes ${dr.best.examples} of ${dr.examples_total} examples.` : 'Nothing built.', dr.uncovered && dr.uncovered.length ? ` Requirements ${dr.uncovered.join(', ')} had no examples, so nothing checked them.` : ''));
    fol.btn.textContent = ALIVE.includes(d.state.state) ? '↓ Jump to live' : '↓ Jump to the end';
    actBody.appendChild(fol.btn); fol.after();
  }

  function renderTasks() {
    const d = S.data; clear(actBody);
    const t = d.tasks;
    actBody.appendChild(h('div', { class: 'note' }, 'Legacy task workflow. The planning interview runs in a terminal: ', h('code', null, `codebot plan ${name}`), '. Freeze and start from the terminal or the Start button.'));
    if (!t) { actBody.appendChild(h('div', { class: 'empty' }, d.plan ? 'The plan is written but not frozen into tasks yet.' : 'No plan yet.')); return; }
    for (const x of t.tasks || []) {
      const blocked = x.status === 'blocked';
      const cls = { done: 'ok', blocked: 'bad', running: 'act' }[x.status] || '';
      const ans = blocked ? h('form', { class: 'row', on: { submit: async e => { e.preventDefault(); const txt = e.target.elements.a.value.trim(); if (!txt) return;
        try { const r = await post(`/api/project/${encodeURIComponent(name)}/answer`, { text: `${x.id}: ${txt}` }); toast(r.out || 'recorded', r.ok ? '' : 'bad'); refresh(true); } catch (er) { toast(er.message, 'bad'); } } } },
        h('input', { name: 'a', placeholder: 'your answer', style: { flex: 1 } }), h('button', { class: 'primary' }, 'Answer')) : null;
      actBody.appendChild(h('div', { class: 'jr' }, h('div', { class: 'jh' }, h('div', { class: 'rn' }, h('b', null, x.id)),
        h('div', { class: 'what' }, h('div', { class: 'l1' }, h('span', { class: ['chip', cls] }, x.status), h('span', null, x.title)),
          h('div', { class: 'l2' }, [x.deps && x.deps.length ? `after ${x.deps.join(', ')}` : '', x.attempts ? plural(x.attempts, 'attempt') : '', x.parent ? `part of ${x.parent}` : ''].filter(Boolean).join(' · ')),
          blocked ? h('div', { class: 'note warn' }, x.blocked_question || 'waiting for an answer') : null, ans),
        h('div', null))));
    }
  }

  async function renderLog() {
    if (!logEl) { clear(actBody); logEl = h('pre', { class: 'logview' }); actBody.append(logEl, fol.btn); }
    try {
      const r = await get(`/api/project/${encodeURIComponent(name)}/log?${q({ offset: logOffset })}`);
      if (logOffset < 0 && r.start > 0) logEl.appendChild(h('span', { class: 'muted' }, `… (the first ${bytes(r.start)} of the log are not shown)\n`));
      if (r.text) { logEl.appendChild(document.createTextNode(r.text)); if (logEl.childNodes.length > 400) logEl.removeChild(logEl.firstChild); }
      logOffset = r.offset; fol.after();
    } catch (e) { if (!logEl.lastChild || !logEl.lastChild.classList) logEl.appendChild(errorBox(e)); }
  }

  async function renderText(which) {
    loading(actBody);
    try {
      const r = await get(`/api/project/${encodeURIComponent(name)}/${which}`);
      const bar = h('div', { class: 'row', style: { padding: '6px 10px' } }, h('button', { type: 'button', on: { click: () => renderText(which) } }, 'Refresh'),
        which === 'audit' ? h('button', { type: 'button', title: 'Tries the network from inside the sandbox to prove it is offline (takes a while)', on: { click: probe } }, 'Run the network probe') : null,
        which === 'audit' && r.warnings ? h('span', { class: 'chip act' }, 'the audit has warnings') : null, which === 'report' ? h('span', { class: 'muted small' }, h('code', null, `codebot report ${name}`)) : h('span', { class: 'muted small' }, h('code', null, `codebot audit ${name}`)));
      replace(actBody, bar, which === 'report' ? md(r.text) : h('pre', { class: 'textblock' }, r.text));
    } catch (e) { replace(actBody, errorBox(e)); }
  }
  async function probe() {
    try { const r = await post(`/api/project/${encodeURIComponent(name)}/audit-probe`, {}); toast(`Network probe started (${r.action.id}); its output is under Machine → actions`); }
    catch (e) { toast(e.message, 'bad'); }
  }

  // ---------- inspector ----------
  const inspHead = h('div', { class: 'panehead' }), inspBody = h('div', { class: 'pane-body' });
  inspPane.append(inspHead, inspBody);
  function pickRound(n, switchPane = true) { S.round = n; S.version = `round:${n}`; S.insp = 'round'; S.roundTab = S.roundTab || 'results'; renderFiles(); renderInspector(); if (switchPane && window.innerWidth < 1100) showPane('inspector'); if (S.actTab === 'rounds') renderJournal(); }

  function inspTitle(...c) {
    const expand = h('button', { type: 'button', class: 'ghost', title: 'Give the inspector the whole width (again to restore)', on: { click: () => {
      const on = !inspPane.classList.contains('expanded'); inspPane.classList.toggle('expanded', on); actPane.hidden = on; filesPane.hidden = on; for (const r of panes.querySelectorAll('.resizer')) r.hidden = on; } } }, '⤢');
    replace(inspHead, h('span', { class: 'title' }, 'Inspector'), h('span', { class: 'grow' }, ...c), expand);
  }

  async function renderInspector() {
    const d = S.data; if (!d) return;
    if (S.insp === 'round' && S.round) return inspectRound(S.round);
    if (S.insp === 'delivered') return inspectDelivered();
    if (S.insp === 'compare') return inspectCompare();
    if (S.insp === 'file' && S.version && S.path) return inspectFile(S.version, S.path);
    return inspectBest();
  }

  function overviewTabs(active) {
    const items = [{ id: 'best', label: 'Best version' }, { id: 'compare', label: 'Compare' }, { id: 'delivered', label: 'Delivered changes' }];
    return tabs(items, active, id => { S.insp = id; renderInspector(); }).el;
  }

  async function inspectBest() {
    const d = S.data, dr = d.state.drafts || {};
    if (d.state.workflow !== 'drafts') { inspTitle(h('span', { class: 'ver' }, 'plan')); replace(inspBody, d.plan ? md(d.plan) : h('div', { class: 'empty' }, 'No plan.')); return; }
    inspTitle(h('span', { class: 'ver' }, dr.best ? `best version · round ${dr.best.round}` : 'no version builds yet'));
    const parts = [overviewTabs('best')];
    if (!dr.best) parts.push(h('div', { class: 'empty' }, dr.started ? 'No round has produced code that builds yet.' : 'Not started.'));
    else {
      const ex = d.best_examples || [];
      const failing = ex.filter(e => e.passed === false).length;
      parts.push(h('div', { class: 'pb', style: { padding: '10px 12px' } },
        h('div', { class: 'row' }, h('span', { class: ['chip', failing ? 'act' : 'ok'] }, `${dr.best.examples}/${dr.examples_total} examples pass`),
          dr.declared_total ? h('span', { class: 'chip' }, `${dr.best.declared}/${dr.declared_total} declared names`) : null,
          dr.best.commit ? h('span', { class: 'chip', title: 'Committed on branch codebot/' + name }, `delivered as ${dr.best.commit.slice(0, 7)}`) : h('span', { class: 'chip act' }, 'not committed (the code folder is not a git repository)')),
        h('p', { class: 'muted small' }, 'Passing the spec\'s examples is not proof of every requirement: only the examples are checked.', dr.uncovered && dr.uncovered.length ? ` Requirements ${dr.uncovered.join(', ')} have no examples at all.` : '')));
      parts.push(examplesTable(ex));
      if (d.best_feedback) parts.push(h('h3', { style: { padding: '10px 12px 0' } }, 'Feedback the model got for this version'), h('pre', { class: 'textblock' }, d.best_feedback));
    }
    replace(inspBody, ...parts);
  }

  async function inspectDelivered() {
    inspTitle(h('span', { class: 'ver' }, `delivered: branch codebot/${name}`)); loading(inspBody);
    try {
      const r = await get(`/api/project/${encodeURIComponent(name)}/delivered`);
      replace(inspBody, overviewTabs('delivered'), h('div', { style: { padding: '10px 12px' } },
        h('p', { class: 'muted small' }, 'What the project changed in your code folder, as commits on its own branch. Your checkout is not touched until you merge.'),
        h('dl', { class: 'kv small' }, h('dt', null, 'code folder'), h('dd', { class: 'path' }, r.workdir || '–'), h('dt', null, 'work folder'), h('dd', { class: 'path' }, r.rundir || '–'), h('dt', null, 'branch'), h('dd', null, h('code', null, r.branch)),
          h('dt', null, 'commit'), h('dd', null, r.commit ? h('code', null, r.commit) : 'none'))),
        h('pre', { class: 'textblock' }, r.summary || ''), r.diff ? diffView(r.diff) : h('div', { class: 'empty' }, 'No differences against your checkout (or the folder is not a git repository).'));
    } catch (e) { replace(inspBody, overviewTabs('delivered'), errorBox(e)); }
  }

  async function inspectCompare() {
    const v = (S.files && S.files.versions) || {};
    const choices = [['best', versionLabel('best', v)], ['latest', versionLabel('latest', v)], ...((v.rounds || []).map(r => [`round:${r}`, `round ${r}`])), ['baseline', 'baseline'], ['snapshot', 'working snapshot']];
    S.cmpA = S.cmpA || 'latest'; S.cmpB = S.cmpB || 'best';
    const pick = (val, set) => h('select', { on: { change: e => { set(e.target.value); inspectCompare(); } } }, choices.map(([k, l]) => h('option', { value: k, selected: k === val }, l)));
    inspTitle(h('span', { class: 'ver' }, `compare ${S.cmpB} → ${S.cmpA}`));
    const bar = h('div', { class: 'row', style: { padding: '8px 12px' } }, 'from', pick(S.cmpB, x => S.cmpB = x), 'to', pick(S.cmpA, x => S.cmpA = x));
    replace(inspBody, overviewTabs('compare'), bar, h('div', { class: 'empty' }, 'Comparing…'));
    try {
      const r = await get(`/api/project/${encodeURIComponent(name)}/compare?${q({ a: S.cmpA, b: S.cmpB })}`);
      const list = r.files.length ? r.files.map(f => h('div', null, h('div', { class: 'insp-head' }, h('b', { class: 'mono' }, f.path), h('span', { class: 'chip' }, f.status)), diffView(f.diff))) : [h('div', { class: 'empty' }, `No differences between ${versionLabel(r.from, v)} and ${versionLabel(r.to, v)}.`)];
      replace(inspBody, overviewTabs('compare'), bar, h('div', { class: 'note' }, `Changes from ${versionLabel(r.from, v)} to ${versionLabel(r.to, v)}.`), ...list);
    } catch (e) { replace(inspBody, overviewTabs('compare'), bar, errorBox(e)); }
  }

  async function inspectFile(version, path) {
    const v = (S.files && S.files.versions) || {};
    inspTitle(h('span', { class: 'ver' }, `${version === 'spec' ? 'specification' : versionLabel(version, v)} · `), h('b', { class: 'mono' }, path));
    loading(inspBody);
    try {
      let r;
      if (version === 'spec') r = { text: S.data.spec || '', size: (S.data.spec || '').length };
      else r = await get(`/api/project/${encodeURIComponent(name)}/file?${q({ v: version, path })}`);
      if (S.version !== version || S.path !== path) return;            // the user picked something else meanwhile
      const notes = [];
      if (version === 'spec' && S.data.state.state !== 'not_started') notes.push(h('div', { class: 'note' }, 'This spec is locked: the run started from it. To change it, ', h('a', { href: `#/new?from=${encodeURIComponent(name)}` }, 'edit it as a new project'), '.'));
      if (r.truncated) notes.push(h('div', { class: 'note warn' }, `Only the first ${bytes(1 << 20)} of ${bytes(r.size)} are shown.`));
      if (r.binary) { replace(inspBody, h('div', { class: 'empty' }, `A binary file (${bytes(r.size)}); not shown.`)); return; }
      const isMd = /\.md$/.test(path) && version !== 'harness' || /^report-.*\.md$/.test(path);
      const toggle = isMd ? h('button', { type: 'button', on: { click: () => { S.raw = !S.raw; inspectFile(version, path); } } }, S.raw ? 'Formatted' : 'Raw') : null;
      replace(inspBody, ...notes, toggle ? h('div', { class: 'row', style: { padding: '6px 10px' } }, toggle) : null, isMd && !S.raw ? md(r.text) : codeView(r.text, path, { wrap: /\.(md|log|txt|jsonl)$/.test(path) }));
    } catch (e) { replace(inspBody, errorBox(e)); }
  }

  async function inspectRound(n) {
    inspTitle(h('span', { class: 'ver' }, `round ${n}`), ' · its prompt, reply, files and results together');
    loading(inspBody);
    try {
      const r = await get(`/api/project/${encodeURIComponent(name)}/round/${n}`);
      if (S.round !== n) return;
      const row = r.row || {};
      const tab = S.roundTab || 'results';
      const tb = tabs([{ id: 'results', label: 'Results' }, { id: 'prompt', label: 'Prompt' }, { id: 'reply', label: 'Reply' }, { id: 'files', label: `Files (${r.files.length})` }], tab,
        id => { S.roundTab = id; show(id); });
      const body = h('div');
      replace(inspBody, tb.el, body);
      const show = id => {
        if (id === 'results') replace(body, h('div', { style: { padding: '10px 12px' } }, h('div', { class: 'row' }, roundOutcome(row), row.fresh ? h('span', { class: 'chip' }, 'fresh draft') : h('span', { class: 'chip' }, 'repair'))),
          r.feedback != null ? [h('div', { class: 'note' }, `Feedback source: ${r.feedback_source}.`), r.examples.length ? examplesTable(r.examples) : null, h('pre', { class: 'textblock' }, r.feedback || '(no feedback: every example passed)')]
            : h('div', { class: 'note' }, 'No recorded feedback for this round (it was the last round of its draft, or the run ended). Its result is in the row above.'));
        else if (id === 'prompt') replace(body, h('pre', { class: 'textblock' }, r.prompt ?? '(not recorded)'));
        else if (id === 'reply') replace(body, h('pre', { class: 'textblock' }, r.reply ?? '(not recorded)'));
        else replace(body, r.files.length ? h('div', { class: 'tree' }, r.files.map(x => h('div', { class: 'node', tabindex: 0, on: { click: () => select(`round:${n}`, x.path) } }, h('span', { class: 'ic' }, '▫'), h('span', { class: 'nm' }, x.path), h('span', { class: 'muted small' }, ' ' + bytes(x.size)))))
          : h('div', { class: 'empty' }, 'This round produced no code.'));
      };
      show(tab);
    } catch (e) { replace(inspBody, errorBox(e)); }
  }

  // ---------- refresh loop ----------
  async function refresh(force) {
    try {
      const r = await get(`/api/project/${encodeURIComponent(name)}/rounds`);
      const changed = force || r.updated !== S.lastUpdated || r.rounds.length !== S.rounds.length || r.state !== (S.data && S.data.state.state) || JSON.stringify(r.phase) !== JSON.stringify(S.data && S.data.phase);
      S.rounds = r.rounds;
      if (changed) {
        S.lastUpdated = r.updated; await loadProject();
        if (!S.files || r.rounds.length !== (S.files.versions || {}).rounds?.length) await loadFiles();
        if (S.actTab === 'rounds') renderActivity(false);
        if (!S.insp) renderInspector();
      }
      if (S.actTab === 'log') await renderLog();
    } catch (e) { if (!S.data) replace(actBody, errorBox(e)); }
  }
  app.onStatus.add(renderHeadIfData); function renderHeadIfData() { if (S.data && !S.busy) renderHead(); }
  stops.push(() => app.onStatus.delete(renderHeadIfData));

  (async () => {
    loading(actBody); loading(inspBody);
    try {
      await loadProject(); await loadFiles(); actTabs.el.firstChild.textContent = S.data.state.workflow === 'tasks' ? 'Tasks' : 'Rounds';
      const r = await get(`/api/project/${encodeURIComponent(name)}/rounds`); S.rounds = r.rounds; S.lastUpdated = r.updated;
      renderActivity(true); renderInspector();
      stops.push(every(2500, () => refresh(false)));
    } catch (e) { replace(root, h('div', { class: 'page' }, errorBox(e), h('a', { href: '#/projects' }, '← Projects'))); }
  })();
  return () => stops.forEach(f => f());
}

function nextName(n) { const m = /^(.*?)-(\d+)$/.exec(n); return m ? `${m[1]}-${+m[2] + 1}` : `${n}-2`; }

// The workshop (files, rounds, log) is one click away. The page a person lands on is the design and the build.
export function mount(root, parts, params, app) {
  if (params.workshop === '1') return workshop(root, parts, params, app);
  const page = h('div');
  root.appendChild(page);
  let inner = null, dead = false;
  const name = parts[0];
  let stopPoll = () => {};
  const open = async () => {
    if (inner || dead) return;
    const d = await get(`/api/project/${encodeURIComponent(name)}`);
    if (dead || inner) return;
    if (d.state.workflow === 'tasks') { page.remove(); inner = workshop(root, parts, params, app) || (() => {}); }
    else { const mod = await import('./conversation.js'); if (!dead) inner = mod.mount(page, parts, params, app); }
    stopPoll();
  };
  stopPoll = every(2500, async () => {
    try { await open(); }
    catch (e) { if (!dead && !inner) replace(page, h('div', { class: 'talk' }, errorBox(e))); }
  });
  return () => { dead = true; stopPoll(); if (typeof inner === 'function') inner(); };
}
