// The front door: describe what you want, answer a few questions, read the plan, build.
// The workshop (rounds, files, logs) stays one link away.
import { h, replace } from '../dom.js';
import { get, post } from '../api.js';
import { confirmDialog, formDialog, toast, every, errorBox } from '../ui.js';

const LEVELS = [
  [0, 'Skip the security check', 'For a throwaway. Nothing is reviewed for safety at the end.'],
  [1, 'This computer only', 'A basic look for commands it should not run, and for files it should not touch.'],
  [2, 'Other computers on your network', 'Also looks for a missing login, and for ways it could gain more access than it should.'],
  [3, 'Anyone on the internet', 'Also checks that one person\'s data stays separate, and that nothing a visitor types can become a command.'],
];
const MARK = { passing: 'Passing', failing: 'Not passing yet', waiting: 'Not tried yet', unchecked: 'No example for this', unbuilt: 'Not tried — it did not build' };
const ALIVE = ['running', 'cooling', 'paused', 'pause_requested'];

export function start(root) {
  document.body.dataset.chrome = 'human';
  const idea = h('textarea', { rows: 8, placeholder: 'A small timer for boiling eggs. It should beep when the time is up, and I want to set the minutes myself.' });
  const name = h('input', { type: 'text', placeholder: 'Leave blank and one will be chosen', autocomplete: 'off' });
  const msg = h('div');
  let busy = false;
  const go = h('button', { type: 'button', class: 'primary', on: { click: submit } }, 'Continue');
  async function submit() {
    if (busy) return;
    busy = true; go.disabled = true; replace(msg);
    try {
      const body = { idea: idea.value };
      if (name.value.trim()) body.name = name.value.trim();
      const r = await post('/api/shape/begin', body);
      location.hash = `#/p/${encodeURIComponent(r.name)}`;
    } catch (e) { replace(msg, h('div', { class: 'note bad' }, e.message)); busy = false; go.disabled = false; }
  }
  root.appendChild(h('div', { class: 'talk' },
    h('h1', null, 'What do you want to build?'),
    h('p', { class: 'lead' }, 'Write it the way you would say it. A few questions, a short plan, then you can build.'),
    idea,
    h('label', { class: 'f', style: { marginTop: '18px' } }, h('span', null, 'Name (optional)'), name),
    h('div', { class: 'row', style: { marginTop: '18px' } }, go),
    msg,
    h('p', { class: 'muted workshop-link' }, h('a', { href: '#/new?spec=1' }, 'I already have a specification'))));
  idea.focus();
  return () => {};
}

export function mount(root, parts, params, app) {
  document.body.dataset.chrome = 'human';
  const name = parts[0];
  const page = h('div', { class: 'talk' });
  root.appendChild(page);
  let sig = '', codeDir = null, hold = {};
  const term = makeTerminal();
  const draw = v => {
    const next = JSON.stringify([v.phase, v.busy, v.error, v.notice, v.security, v.build_state, v.understood,
      (v.questions || []).map(q => q.id + q.ask), (v.checklist || []).map(c => c.status + c.detail),
      v.outline && v.outline.summary, v.review && JSON.stringify(v.review), v.terminal && v.terminal.show, v.terminal && v.terminal.label, v.terminal && v.terminal.window,
      (v.attention || []).map(a => a.key + a.kind)]);
    if (next === sig) return;
    sig = next; render(v);
  };
  async function load() {
    try { draw(await get(`/api/project/${encodeURIComponent(name)}/shape`)); }
    catch (e) { replace(page, errorBox(e)); }
  }
  function updates(v) {
    const items = (v.attention || []).filter(a => a.kind === 'finished' || a.kind === 'crashed');
    if (!items.length) return null;
    return h('section', { class: 'updates' },
      h('h2', null, 'Updates'),
      h('ul', { class: 'updates-list' }, items.map(a => h('li', null,
        h('span', { class: ['attn-badge', a.kind] }, a.kind === 'finished' ? 'Finished' : 'Interrupted'),
        h('span', null, a.text),
        a.kind === 'finished' ? h('button', { type: 'button', class: 'ghost', on: { click: async () => {
          try { await post('/api/inbox/ack', { key: a.key }); sig = ''; await load(); } catch (e) { toast(e.message, 'bad'); }
        } } }, 'Dismiss') : null))));
  }
  function render(v) {
    const title = (v.outline && v.outline.title) || v.title || name;
    if (v.busy) return replace(page, h('h1', null, title), h('p', { class: 'lead' }, v.busy + '. This usually takes a minute.'), v.idea ? h('p', { class: 'muted' }, v.idea) : null);
    const bits = [updates(v)];
    if (v.error) bits.push(h('div', { class: 'note bad' }, v.error), h('div', { class: 'row' }, h('button', { type: 'button', on: { click: () => act('retry', {}) } }, 'Try again')));
    if (v.phase === 'questions') bits.push(questions(v));
    else if (v.phase === 'ready' && !v.plain) bits.push(plan(v));
    else if (v.phase === 'review') bits.push(h('h1', null, title), h('p', { class: 'lead' }, 'The build has stopped. Looking through the code before calling it done.'), runBox(v));
    else bits.push(progress(v, title));
    replace(page, bits);
  }
  function runBox(v) {
    if (!(v.terminal && v.terminal.show) || ['questions', 'ready', 'describe'].includes(v.phase)) return null;
    term.setLabel(v.terminal.label, v.terminal.window);
    queueMicrotask(() => term.wake());
    return term.el;
  }
  function makeTerminal() {
    const screen = h('pre', { class: 'screen', role: 'log', 'aria-label': 'Program output' });
    const line = h('input', { type: 'text', placeholder: 'Type here and press Enter', autocomplete: 'off', disabled: true });
    const pane = h('div', { class: 'pane', hidden: true }, screen, line);
    const runBtn = h('button', { type: 'button', class: 'primary' }, 'Run');
    const stopBtn = h('button', { type: 'button', hidden: true }, 'Stop');
    const label = h('span', { class: 'muted small' });
    const note = h('span', { class: 'muted small', hidden: true }, 'It may also open its own window.');
    let since = 0, running = false, opened = false, timer = null;
    function reveal() { opened = true; pane.hidden = false; }
    async function pull() {
      const r = await get(`/api/project/${encodeURIComponent(name)}/run?since=${since}`);
      if (r.running) reveal();
      if (r.output) { screen.textContent += r.output; since = r.next; screen.scrollTop = screen.scrollHeight; }
      running = !!r.running;
      runBtn.hidden = running; stopBtn.hidden = !running; line.disabled = !running;
    }
    function loop() {
      if (!screen.isConnected) { timer = null; return; }
      pull().catch(() => {}).finally(() => { if (screen.isConnected) timer = setTimeout(loop, running ? 400 : 1500); });
    }
    runBtn.addEventListener('click', async () => {
      reveal(); screen.textContent = ''; since = 0;
      try {
        await post(`/api/project/${encodeURIComponent(name)}/run`, { action: 'start' });
        running = true; runBtn.hidden = true; stopBtn.hidden = false; line.disabled = false; line.focus();
        if (!timer) loop(); else pull().catch(e => toast(e.message, 'bad'));
      } catch (e) { toast(e.message, 'bad'); }
    });
    stopBtn.addEventListener('click', async () => {
      try { await post(`/api/project/${encodeURIComponent(name)}/run`, { action: 'stop' }); }
      catch (e) { toast(e.message, 'bad'); }
    });
    line.addEventListener('keydown', async e => {
      if (e.key !== 'Enter' || line.disabled) return;
      const text = line.value; line.value = '';
      try { await post(`/api/project/${encodeURIComponent(name)}/run`, { action: 'input', text }); }
      catch (err) { toast(err.message, 'bad'); }
    });
    return { el: h('section', { class: 'term' }, h('div', { class: 'termbar' }, runBtn, stopBtn, label, note), pane),
      setLabel(s, windowed) { label.textContent = s || ''; note.hidden = !windowed; },
      wake() { if (opened) pane.hidden = false; if (!timer && screen.isConnected) loop(); } };
  }
  function questions(v) {
    const boxes = {};
    const block = h('div', null,
      h('h1', null, 'A few things that would change the design'),
      v.understood ? h('p', { class: 'lead' }, v.understood) : null,
      (v.questions || []).map(q => {
        const box = h('textarea', { rows: 3 }, q.answer || '');
        boxes[q.id] = box;
        return h('div', { class: 'q' }, h('h2', null, q.ask), q.why ? h('p', null, q.why) : null, box);
      }),
      h('div', { class: 'row' },       h('button', { type: 'button', class: 'primary', on: { click: () => {
        const answers = {}; for (const [id, el] of Object.entries(boxes)) answers[id] = el.value;
        act('answer', { answers });
      } } }, 'Continue')));
    return block;
  }
  function plan(v) {
    const o = v.outline || {};
    const code = h('input', { type: 'text', value: codeDir ?? v.code_dir ?? '', style: { width: '100%' }, on: { input: e => { codeDir = e.target.value; } } });
    const minutes = h('input', { type: 'number', min: 1, max: 1440, placeholder: '60', value: hold.minutes || '', on: { input: e => { hold.minutes = e.target.value; } } });
    const seed = h('input', { type: 'number', min: 0, placeholder: '1', value: hold.seed || '', on: { input: e => { hold.seed = e.target.value; } } });
    const note = h('textarea', { rows: 3, placeholder: 'Anything to add or change', on: { input: e => { hold.note = e.target.value; } } }, hold.note || '');
    let security = v.security ?? 1;
    const cards = LEVELS.map(([n, label, hint]) => {
      const b = h('button', { type: 'button', class: ['choice', n === security ? 'on' : ''], on: { click: async () => {
        security = n; cards.forEach((c, i) => c.classList.toggle('on', LEVELS[i][0] === n));
        try { await post(`/api/project/${encodeURIComponent(name)}/shape/security`, { security: n }); }
        catch (e) { toast(e.message, 'bad'); }
      } } }, h('b', null, label), h('span', { class: 'muted' }, hint));
      return b;
    });
    const suggest = v.suggest_security != null && v.suggest_security > security
      ? h('p', { class: 'note warn' }, 'From what you described, other people may be able to reach this. A higher check is in the list above.') : null;
    return h('div', null,
      h('h1', null, o.title || name),
      h('p', { class: 'lead' }, 'This is enough to build. Read it, add anything that is missing, or build it.'),
      o.summary ? h('p', null, o.summary) : null,
      (o.pieces || []).length ? h('ul', { class: 'pieces' }, o.pieces.map(p => h('li', null, h('b', null, p.name), p.does ? ' — ' + p.does : ''))) : null,
      (o.decisions || []).length ? h('div', null, h('h2', null, 'Decided'), h('ul', null, o.decisions.map(d => h('li', null, d)))) : null,
      (v.open || []).length ? h('div', { class: 'note warn' }, h('b', null, 'Still open. '), 'The build will go ahead with a reasonable reading of these: ', v.open.join(' ')) : null,
      gaps(v),
      h('h2', { style: { marginTop: '18px' } }, 'Before it is called done'),
      h('div', { class: 'choices' }, cards),
      suggest,
      h('label', { class: 'f', style: { marginTop: '14px' } }, h('span', null, 'The code will live here'), code),
      h('p', { class: 'muted small' }, 'The folder is created if it is not there. It stays outside codebot, on its own branch.'),
      h('label', { class: 'f', style: { marginTop: '12px' } }, h('span', null, 'Add a note'), note),
      h('div', { class: 'row', style: { marginTop: '8px' } },
        h('button', { type: 'button', on: { click: () => act('clarify', { text: note.value }) } }, 'Update the plan'),
        h('button', { type: 'button', class: 'primary', on: { click: () => act('build', { code_dir: code.value, security, overrides: overrides(minutes, seed) }) } }, 'Build')),
      h('details', { class: 'adv' }, h('summary', null, 'Build settings'),
        h('div', { class: 'grid2', style: { marginTop: '8px' } },
          h('label', { class: 'f' }, h('span', null, 'Time budget, minutes'), minutes),
          h('label', { class: 'f' }, h('span', null, 'Seed'), seed)),
        v.spec ? h('pre', { class: 'textblock', style: { marginTop: '10px' } }, v.spec) : null));
  }
  function progress(v, title) {
    const rows = v.checklist || [];
    const score = v.score || {};
    const running = ALIVE.includes(v.build_state);
    const headline = v.notice || (v.phase === 'done' ? 'It is ready.' : running ? 'Working through the checks.' : 'Not running.');
    return h('div', null,
      h('h1', null, title),
      runBox(v),
      h('p', { class: 'lead' }, headline),
      v.phase === 'done' ? findings(v.review) : null,
      score.total ? h('p', null, `${score.passing} of ${score.total} checks passing.`) : null,
      checklistView(rows),
      h('div', { class: 'row', style: { marginTop: '16px' } }, controls(v, running)),
      h('p', { class: 'workshop-link muted' }, h('a', { href: `#/p/${encodeURIComponent(name)}?workshop=1` }, 'Open workshop'), ' — rounds, files, and logs'));
  }
  function findings(review) {
    if (!review) return null;
    // the second look re-reads the serious notes: what it keeps replaces them, what it drops is gone
    const first = [...((review.security || {}).findings || []), ...((review.code || {}).findings || [])];
    const all = review.again ? [...first.filter(f => f.serious !== 'serious'), ...(review.again.findings || [])] : first;
    if (!all.length) return null;
    const rank = { serious: 0, real: 1, low: 2 };
    all.sort((a, b) => (rank[a.serious] ?? 3) - (rank[b.serious] ?? 3));
    return h('div', { class: 'points' }, all.map(f => h('div', { class: 'point' }, h('span', { class: 'mark' }, f.serious === 'low' ? 'minor' : f.serious), h('div', null, h('b', null, f.where), ' ', f.what))));
  }
  function checklistView(rows) {
    const point = r => h('div', { class: ['point', r.status] }, h('span', { class: 'mark' }, MARK[r.status] || r.status), h('div', null, h('div', null, r.title), r.detail ? h('div', { class: 'muted small' }, r.detail) : null));
    const checked = rows.filter(r => r.status !== 'unchecked'), unchecked = rows.filter(r => r.status === 'unchecked');
    return h('div', null,
      checked.length ? h('div', { class: 'points' }, checked.map(point)) : null,
      unchecked.length ? h('details', { class: 'unchecked' }, h('summary', null, checked.length ? `${unchecked.length} more ${unchecked.length === 1 ? 'part has' : 'parts have'} no example, so nothing checked ${unchecked.length === 1 ? 'it' : 'them'}`
        : `Nothing in this plan could be checked: none of its ${unchecked.length} parts has an example`), h('ul', null, unchecked.map(r => h('li', null, r.title)))) : null);
  }
  function gaps(v) {
    const missing = (v.checklist || []).filter(r => r.status === 'unchecked');
    if (!missing.length) return null;
    const lead = missing.length === 1
      ? 'One part of the plan has no example, so the build cannot check it: '
      : 'Some parts of the plan have no example, so the build cannot check them: ';
    return h('div', { class: 'note warn' }, lead, missing.map(r => r.title).join('; '), '.');
  }
  function overrides(minutes, seed) {
    const o = {};
    if (minutes.value) { o.DRAFTS_MINUTES = minutes.value; hold.minutes = minutes.value; }
    if (seed.value) { o.DRAFTS_SEED = seed.value; hold.seed = seed.value; }
    return o;
  }
  function controls(v, running) {
    const out = [];
    const j = (app.status || {}).job;
    const blocked = j && !(j.kind === 'project' && j.name === name);
    if (['stopped', 'not_started', 'crashed'].includes(v.build_state) && v.phase !== 'done') {
      out.push(h('button', { type: 'button', class: 'primary', disabled: !!blocked, title: blocked ? 'The model is already in use.' : '', on: { click: () => run('start') } }, 'Start'));
    }
    if (v.build_state === 'running' || v.build_state === 'cooling') out.push(h('button', { type: 'button', on: { click: () => run('pause') } }, 'Pause'));
    if (v.build_state === 'paused' || v.build_state === 'pause_requested') out.push(h('button', { type: 'button', class: 'primary', on: { click: () => run('resume') } }, 'Resume'));
    if (running) out.push(h('button', { type: 'button', class: 'danger', on: { click: stop } }, 'Stop'));
    if (running) out.push(h('button', { type: 'button', on: { click: moreTime } }, 'More time'));
    return out;
  }
  async function act(what, body) {
    try {
      const r = await post(`/api/project/${encodeURIComponent(name)}/shape/${what}`, body);
      if (r && r.ok === false) toast(r.out || 'That did not work', 'bad');
      sig = ''; await load();
    } catch (e) { toast(e.message, 'bad'); }
  }
  async function run(what) {
    try { const r = await post(`/api/project/${encodeURIComponent(name)}/${what}`, {}); if (!r.ok) toast(r.out || 'That did not work', 'bad'); }
    catch (e) { toast(e.message, 'bad'); }
    sig = ''; await load();
  }
  async function stop() {
    const ok = await confirmDialog({ title: 'Stop this build?', confirm: 'Stop', danger: true, body: h('p', null, 'The work so far stays. Pausing lets you continue; stopping ends it.') });
    if (!ok) return;
    try { await post(`/api/project/${encodeURIComponent(name)}/stop`, { confirm: true }); }
    catch (e) { toast(e.message, 'bad'); }
    sig = ''; await load();
  }
  async function moreTime() {
    const v = await formDialog({ title: 'Give the build more time', submit: 'Set it', fields: [{ name: 'minutes', label: 'Minutes for the whole build', type: 'number', value: 120, min: 1, max: 1440 }],
      validate: x => (Number(x.minutes) >= 1 ? null : 'At least a minute') });
    if (!v) return;
    try { await post(`/api/project/${encodeURIComponent(name)}/budget`, { minutes: v.minutes }); toast('It will use the new time from the next check.'); }
    catch (e) { toast(e.message, 'bad'); }
  }
  return every(2500, load);
}
