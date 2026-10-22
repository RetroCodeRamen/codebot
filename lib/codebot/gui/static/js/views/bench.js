// Benchmarks: runs, one run's trials with hidden-test scores, start / resume / cancel / validate. Every action is a `codebot bench` command.
import { h, replace, when, bytes } from '../dom.js';
import { get, post } from '../api.js';
import { every, errorBox, toast, confirmDialog } from '../ui.js';
import { render as md } from '../markdown.js';

export function mount(root, parts, params, app) {
  const page = h('div', { class: 'page wide' }); root.appendChild(page);
  return parts[0] ? runView(page, parts[0], app) : listView(page, app);
}

function listView(page, app) {
  const list = h('div'), form = h('div');
  replace(page, h('div', { class: 'pagehead' }, h('h1', null, 'Benchmarks'), h('span', { class: 'muted' }, 'the Capability Ladder: hidden tests the model never sees')),
    h('div', { class: 'grid2' }, h('div', { class: 'panel' }, h('div', { class: 'ph' }, h('h2', null, 'Runs')), h('div', { class: 'pb' }, list)), form));
  buildForm(form, app);
  return every(6000, async () => {
    try {
      const r = await get('/api/bench/runs');
      replace(list, r.runs.length ? h('table', { class: 't' }, h('thead', null, h('tr', null, ['run', 'status', 'mode', 'trials', 'minutes per task', 'created'].map(c => h('th', null, c)))),
        h('tbody', null, r.runs.map(x => h('tr', { class: 'click', on: { click: () => location.hash = `#/bench/${encodeURIComponent(x.run_id)}` } },
          h('td', { class: 'mono small' }, x.run_id), h('td', null, runChip(x.status, x.runner_alive)),
          h('td', null, x.mode), h('td', { class: 'num' }, String(x.trials)), h('td', { class: 'num' }, String((x.config || {}).task_minutes ?? '')), h('td', { class: 'small muted' }, when(x.created)))))) : h('div', { class: 'muted' }, 'No benchmark runs yet.'));
    } catch (e) { replace(list, errorBox(e)); }
  });
}

// A run whose state says running but has no live runner has stalled (the same rule as `codebot bench list`); Resume continues it.
function runChip(status, alive) {
  if (status === 'running' && !alive) return h('span', { class: 'chip bad', title: 'Its state says running, but no benchmark runner is alive (a crash or a reboot). Resume continues it.' }, 'stalled');
  return h('span', { class: ['chip', { finished: 'ok', running: 'act', failed: 'bad' }[status] || ''] }, status || '?');
}

async function buildForm(box, app) {
  let levels = [];
  try { levels = (await get('/api/bench/levels')).levels; } catch (e) { /* shown below */ }
  const target = h('select', null, levels.map(l => h('option', { value: l.id }, `${l.id}: ${l.title || ''}`)), h('option', { value: 'ladder' }, 'the whole ladder (levels 1-12)'), h('option', { value: 'growing' }, 'the growing project'));
  const f = { mode: h('select', null, ['drafts', 'full', 'reference-plan', 'minimal'].map(m => h('option', { value: m }, m))), repeats: h('input', { type: 'number', min: 1, max: 20, value: 1 }),
    task_minutes: h('input', { type: 'number', min: 1, max: 1440, value: 45 }), seed: h('input', { type: 'number', min: 0, placeholder: '1' }), model: h('input', { type: 'text', placeholder: 'default model' }),
    num_ctx: h('input', { type: 'number', placeholder: 'default' }), name: h('input', { type: 'text', placeholder: 'optional label' }) };
  const out = h('div');
  const go = async () => {
    const j = (app.status || {}).job;
    if (j) { toast(`The model is busy: ${j.kind} ${j.name || ''}. One model job at a time.`, 'bad'); return; }
    const body = { target: target.value, no_notify: true }; for (const [k, el] of Object.entries(f)) body[k] = el.value;
    try { const r = await post('/api/bench/start', body); replace(out, h('pre', { class: 'textblock' }, r.out)); toast(r.ok ? 'Benchmark started in the background' : 'not started', r.ok ? '' : 'bad'); }
    catch (e) { replace(out, errorBox(e)); }
  };
  const validate = async () => { try { const r = await post('/api/bench/validate', {}); toast(`Validation started (${r.action.id}); see Machine → actions`); } catch (e) { toast(e.message, 'bad'); } };
  replace(box, h('div', { class: 'panel' }, h('div', { class: 'ph' }, h('h2', null, 'Start a run')), h('div', { class: 'pb stack' },
    h('label', { class: 'f' }, h('span', null, 'what to run'), target),
    h('div', { class: 'grid3' }, h('label', { class: 'f' }, h('span', null, 'mode'), f.mode), h('label', { class: 'f' }, h('span', null, 'repeats (seeds)'), f.repeats), h('label', { class: 'f' }, h('span', null, 'minutes per task'), f.task_minutes),
      h('label', { class: 'f' }, h('span', null, 'first seed'), f.seed), h('label', { class: 'f' }, h('span', null, 'model'), f.model), h('label', { class: 'f' }, h('span', null, 'context (num_ctx)'), f.num_ctx)),
    h('label', { class: 'f' }, h('span', null, 'run label'), f.name),
    h('p', { class: 'muted small' }, 'Minutes per task is the benchmark\'s own budget (in drafts mode: the whole trial). It is separate from a project\'s DRAFTS_MINUTES. A benchmark is a model job: nothing else can use the model while it runs.'),
    h('div', { class: 'row' }, h('button', { type: 'button', class: 'primary', on: { click: go } }, 'Start in the background'), h('button', { type: 'button', on: { click: validate }, title: 'The benchmark\'s own oracle self-test; no model' }, 'Validate the benchmark')), out)));
}

function runView(page, rid, app) {
  const draw = async () => {
    try {
      const r = await get(`/api/bench/run/${encodeURIComponent(rid)}`); const s = r.state;
      const stalled = s.status === 'running' && !r.runner_alive;
      const ctl = h('div', { class: 'row' },
        r.runner_alive ? h('button', { type: 'button', class: 'danger', on: { click: cancel } }, 'Cancel…') : null,
        stalled || ['cancelled', 'failed', 'interrupted'].includes(s.status) ? h('button', { type: 'button', on: { click: resume } }, 'Resume') : null);
      replace(page, h('div', { class: 'pagehead' }, h('a', { href: '#/bench' }, '← Benchmarks'), h('h1', { class: 'mono' }, rid), runChip(s.status, r.runner_alive), h('span', { class: 'grow' }), ctl),
        h('div', { class: 'panel' }, h('div', { class: 'pb' }, h('dl', { class: 'kv' }, h('dt', null, 'mode'), h('dd', null, s.mode), h('dt', null, 'suite'), h('dd', null, s.suite), h('dt', null, 'minutes per task'), h('dd', null, String((s.config || {}).task_minutes)),
          h('dt', null, 'model'), h('dd', null, (s.config || {}).model || ''), h('dt', null, 'settings'), h('dd', { class: 'mono small' }, JSON.stringify((s.config || {}).roles || {})), h('dt', null, 'created'), h('dd', null, when(s.created))))),
        h('div', { class: 'panel', style: { marginTop: '14px' } }, h('div', { class: 'ph' }, h('h2', null, 'Trials')), h('table', { class: 't' },
          h('thead', null, h('tr', null, ['trial', 'outcome', 'hidden tests', 'by requirement', 'spec examples', 'time'].map(c => h('th', null, c)))),
          h('tbody', null, r.trials.map(t => h('tr', null, h('td', { class: 'mono' }, t.id), h('td', null, h('span', { class: ['chip', t.outcome === 'COMPLETE' ? 'ok' : t.outcome ? '' : 'act'] }, t.outcome || t.phase || '?')),
            h('td', { class: 'num' }, t.total ? `${t.passed}/${t.total}` : '–'), h('td', { class: 'small mono' }, t.by_requirement ? Object.entries(t.by_requirement).map(([k, v]) => `${k} ${v.passed}/${v.total}`).join('  ') : ''),
            h('td', { class: 'num' }, t.drafts ? `${t.drafts.delivered_examples ?? '–'}/${t.drafts.examples_total ?? '–'}` : '–'), h('td', { class: 'num' }, t.drafts ? `${t.drafts.minutes ?? '–'} min` : (t.timing ? `${Math.round((t.timing.run_s || 0) / 60)} min` : '–'))))))),
        r.report ? h('div', { class: 'panel', style: { marginTop: '14px' } }, h('div', { class: 'ph' }, h('h2', null, 'Report')), md(r.report)) : null);
    } catch (e) { replace(page, h('a', { href: '#/bench' }, '← Benchmarks'), errorBox(e)); }
  };
  const cancel = async () => {
    if (!await confirmDialog({ title: `Cancel ${rid}?`, danger: true, confirm: 'Cancel the run', body: h('p', null, 'The runner stops the current trial\'s loop at its next poll. Finished trials and consumed time are kept; it can be resumed later.') })) return;
    try { const r = await post(`/api/bench/run/${encodeURIComponent(rid)}/cancel`, { confirm: true }); toast(r.out || 'cancel requested', r.ok ? '' : 'bad'); } catch (e) { toast(e.message, 'bad'); }
    draw();
  };
  const resume = async () => {
    const j = (app.status || {}).job; if (j) { toast(`The model is busy: ${j.kind} ${j.name || ''}`, 'bad'); return; }
    try { const r = await post(`/api/bench/run/${encodeURIComponent(rid)}/resume`, {}); toast(r.out || 'resumed', r.ok ? '' : 'bad'); } catch (e) { toast(e.message, 'bad'); }
    draw();
  };
  return every(8000, draw);
}
