// Modular team pipeline: canvas, JSON, monitor. Canonical config is pipeline/project.json.
import { h, replace } from '../dom.js';
import { get, post } from '../api.js';
import { every } from '../ui.js';
import { teamCanvas, workflowCanvas } from './pipeline-canvas.js';

function memberCard(m, agents, run) {
  const ag = agents[m.agent] || {};
  const st = ((run || {}).members || {})[m.id] || {};
  return h('div', { class: 'panel', style: 'margin:0.5rem 0' },
    h('div', { class: 'ph' }, h('strong', null, m.id), ' · ', ag.name || m.agent,
      st.state ? h('span', { class: 'pill', style: 'margin-left:0.5rem' }, st.state) : null),
    h('div', { class: 'pb small' }, ag.goal || '',
      h('div', { class: 'muted' }, 'talks to: ', (m.can_talk_to || []).join(', ') || '—'),
      h('div', { class: 'muted' }, 'memory: ', ag.memory || 'task', ' · sees write: ', ((ag.sees || {}).write || []).join(', ') || '—')));
}

export function mount(el, parts) {
  const name = parts[0];
  if (!name) { replace(el, h('div', { class: 'page wide' }, h('div', { class: 'empty' }, 'Pick a project.'))); return; }
  let tab = 'team', raw = '', stopPoll = null, selected = null, doc = null;

  async function load() {
    const view = await get(`/api/project/${encodeURIComponent(name)}/pipeline`);
    try {
      const j = await get(`/api/project/${encodeURIComponent(name)}/pipeline/json`);
      if (j && j.json) { raw = j.json; try { doc = JSON.parse(raw); } catch (e) { doc = null; } }
    } catch (e) { /* no json yet */ }
    render(view);
  }

  function b(label, cls, fn) {
    return h('button', { type: 'button', class: cls || '', on: { click: fn } }, label);
  }

  async function saveDoc(next) {
    doc = next; raw = JSON.stringify(doc, null, 1);
    const v = await post(`/api/project/${encodeURIComponent(name)}/pipeline/validate`, { json: raw });
    if (!v.ok) { alert((v.errors || ['invalid']).join('\n')); return false; }
    await post(`/api/project/${encodeURIComponent(name)}/pipeline/save`, { json: raw });
    return true;
  }

  function syncTalkAndLinks(ma, otherId, kind) {
    // kind null = remove; agent|repository|both = set. Talk rights follow agent|both.
    const links = (ma.links || []).filter(l => l.to !== otherId);
    const talk = new Set(ma.can_talk_to || []);
    talk.delete(otherId);
    if (kind) {
      links.push({ kind, to: otherId });
      if (kind === 'agent' || kind === 'both') talk.add(otherId);
    }
    ma.links = links;
    ma.can_talk_to = [...talk];
  }

  async function toggleLink(a, b) {
    if (!doc || !doc.team) return;
    const members = doc.team.members || [];
    const ma = members.find(m => m.id === a), mb = members.find(m => m.id === b);
    if (!ma || !mb) return;
    const existing = (ma.links || []).find(l => l.to === b);
    const order = [null, 'agent', 'both', 'repository'];
    let i = 0;
    if (existing) i = Math.max(0, order.indexOf(existing.kind || 'agent'));
    else if ((ma.can_talk_to || []).includes(b)) i = 1;
    const next = order[(i + 1) % order.length];
    syncTalkAndLinks(ma, b, next);
    if (!(await saveDoc(doc))) return;
    selected = a;
    await load();
  }

  async function setLinkKind(a, b, kind) {
    if (!doc || !doc.team) return;
    const ma = (doc.team.members || []).find(m => m.id === a);
    if (!ma) return;
    syncTalkAndLinks(ma, b, kind);
    if (!(await saveDoc(doc))) return;
    await load();
  }

  async function savePositions(positions) {
    if (!doc) return;
    doc.ui = doc.ui || {};
    doc.ui.team = doc.ui.team || {};
    doc.ui.team.positions = positions;
    await saveDoc(doc);
  }

  function render(view) {
    const run = view.run || {};
    const team = (view.bundle || {}).team || (doc && doc.team) || {};
    const agents = (view.bundle || {}).agents || (doc && doc.agents) || {};
    const wf = (view.bundle || {}).workflow || (doc && doc.workflow) || {};
    const ui = (view.bundle && view.bundle.ui) || (doc && doc.ui) || {};

    const tabs = h('div', { class: 'row' },
      b('Team', tab === 'team' ? 'primary' : '', () => { tab = 'team'; render(view); }),
      b('Workflow', tab === 'workflow' ? 'primary' : '', () => { tab = 'workflow'; render(view); }),
      b('JSON', tab === 'json' ? 'primary' : '', () => { tab = 'json'; render(view); }),
      b('Monitor', tab === 'run' ? 'primary' : '', () => { tab = 'run'; render(view); }));

    const actions = h('div', { class: 'row', style: 'margin:0.75rem 0; gap:0.5rem; flex-wrap:wrap' },
      b('Design team', 'primary', async () => { await post(`/api/project/${encodeURIComponent(name)}/pipeline/design`, {}); await load(); }),
      b('Step once', '', async () => { await post(`/api/project/${encodeURIComponent(name)}/pipeline/step`, {}); await load(); }),
      b('Run', 'primary', async () => { await post(`/api/project/${encodeURIComponent(name)}/pipeline/run`, {}); await load(); }),
      b('Cancel', '', async () => { await post(`/api/project/${encodeURIComponent(name)}/pipeline/cancel`, {}); await load(); }),
      (run.status === 'WAITING_APPROVAL'
        ? b('Approve', 'primary', async () => { await post(`/api/project/${encodeURIComponent(name)}/pipeline/approve`, {}); await load(); })
        : null),
      h('a', { href: `#/p/${encodeURIComponent(name)}` }, 'Back to project'));

    let body;
    if (view.error && !view.bundle && !doc) {
      body = h('div', { class: 'note bad' }, view.error || 'No pipeline yet.',
        h('p', { class: 'muted' }, 'Install a template, run Design team, or paste JSON.'),
        h('div', { class: 'row', style: 'gap:0.5rem;flex-wrap:wrap;margin-top:0.75rem' },
          b('Solo', 'primary', async () => { await post(`/api/project/${encodeURIComponent(name)}/pipeline/init`, { template: 'solo', force: true }); await load(); }),
          b('Reviewers', '', async () => { await post(`/api/project/${encodeURIComponent(name)}/pipeline/init`, { template: 'architect-reviewers', force: true }); await load(); }),
          b('Debate', '', async () => { await post(`/api/project/${encodeURIComponent(name)}/pipeline/init`, { template: 'debate', force: true }); await load(); }),
          b('Small team', '', async () => { await post(`/api/project/${encodeURIComponent(name)}/pipeline/init`, { template: 'small-team', force: true }); await load(); })));
    } else if (tab === 'team') {
      const sel = selected ? (team.members || []).find(m => m.id === selected) : null;
      const linkBar = sel ? h('div', { class: 'row', style: 'gap:0.35rem;flex-wrap:wrap;margin:0.5rem 0' },
        h('span', { class: 'muted small' }, `Links from ${selected}: click another node to cycle none→agent→both→repository. Or set:`),
        ...(team.members || []).filter(m => m.id !== selected).map(m => {
          const cur = ((sel.links || []).find(l => l.to === m.id) || {}).kind
            || ((sel.can_talk_to || []).includes(m.id) ? 'agent' : '—');
          return h('span', { class: 'row', style: 'gap:0.2rem;align-items:center' },
            h('span', { class: 'muted small' }, m.id, ' (', cur, ')'),
            b('agent', '', () => setLinkKind(selected, m.id, 'agent')),
            b('both', '', () => setLinkKind(selected, m.id, 'both')),
            b('repo', '', () => setLinkKind(selected, m.id, 'repository')),
            b('×', '', () => setLinkKind(selected, m.id, null)));
        })) : null;
      body = h('div', null,
        teamCanvas({
          team, agents, run, ui, selected,
          onSelect: id => { selected = id; render(view); },
          onToggleLink: toggleLink,
          onDragEnd: savePositions
        }),
        linkBar,
        h('h3', null, 'Agents'),
        ...(team.members || []).map(m => memberCard(m, agents, run)));
    } else if (tab === 'workflow') {
      body = h('div', null,
        workflowCanvas({ workflow: wf, run, ui }),
        h('p', { class: 'muted small' }, 'Workflow graph is read-only here; edit nodes in the JSON tab.'));
    } else if (tab === 'json') {
      const ta = h('textarea', { class: 'mono', style: 'width:100%;min-height:24rem' });
      ta.value = raw;
      body = h('div', null, ta,
        h('div', { class: 'row', style: 'margin-top:0.5rem' },
          b('Validate & save', 'primary', async () => {
            raw = ta.value;
            try { doc = JSON.parse(raw); } catch (e) { alert('bad JSON: ' + e.message); return; }
            if (!(await saveDoc(doc))) return;
            await load();
          })));
    } else {
      body = h('div', null,
        h('dl', { class: 'kv' },
          h('dt', null, 'status'), h('dd', null, run.status || '—'),
          h('dt', null, 'cursor'), h('dd', { class: 'mono' }, run.cursor || '—'),
          h('dt', null, 'active'), h('dd', { class: 'mono' }, run.active || '—'),
          h('dt', null, 'queue'), h('dd', { class: 'mono' }, JSON.stringify(run.queue || [])),
          h('dt', null, 'steps'), h('dd', null, String(run.step_count ?? '—')),
          h('dt', null, 'error'), h('dd', null, run.error || '—')),
        h('h3', null, 'Members'),
        h('ul', { class: 'small' }, ...Object.entries(run.members || {}).map(([id, m]) =>
          h('li', null, id, ': ', m.state, m.node ? ` @ ${m.node}` : '', m.wait_for ? ` waiting ${m.wait_for}` : ''))),
        h('h3', null, 'Recent messages'),
        h('ul', { class: 'small mono' }, ...(view.messages || []).slice(-12).map(m =>
          h('li', null, `${m.type} ${m.from}→${m.to}: ${JSON.stringify(m.payload).slice(0, 120)}`))),
        h('h3', null, 'Artifacts'),
        h('ul', { class: 'small' }, ...(view.artifacts || []).map(a => h('li', null, a.id, ' ', a.kind))));
    }

    replace(el, h('div', { class: 'page wide' },
      h('h1', null, 'Pipeline · ', name),
      actions, tabs, body));
  }

  load();
  stopPoll = every(3000, () => { if (tab === 'run') load(); });
  return () => { if (stopPoll) stopPoll(); };
}
