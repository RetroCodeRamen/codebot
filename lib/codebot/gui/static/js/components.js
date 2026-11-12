// Pieces several screens share: state chips, code and diff views, the examples table, version labels.
import { h, svg } from './dom.js';
import { tokenize, langOf } from './highlight.js';

export const STATE = {
  running: ['Running', 'act', 'Working on this project'],
  cooling: ['Cooling down', 'act', 'CPU is hot — waiting to cool before the next model call. This is not a pause.'],
  pause_requested: ['Pausing…', 'act', 'Will pause when the current step finishes'],
  paused: ['Paused', '', 'Waiting — Resume continues'],
  stopped: ['Stopped', '', 'Not running'],
  finished: ['Finished', 'ok', 'This run is over; start again with a new project'],
  crashed: ['Interrupted', 'bad', 'Marked active, but the process is gone. The watchdog can restart it if installed.'],
  not_started: ['Not started', '', 'Created but never started'],
  shaping: ['Designing', 'act', 'Working out what this project is'],
};
export function stateChip(state, extra) {
  const [label, cls, title] = STATE[state] || [state || 'unknown', '', ''];
  return h('span', { class: ['chip', cls, state === 'running' || state === 'cooling' ? 'pulse' : ''], title }, extra ? `${label} · ${extra}` : label);
}
export const STOPS = { 'examples pass': 'every example passes', 'time cap': 'time budget used up', 'round cap': 'round limit reached' };
// With no runnable examples a run stops at the first version that builds: that is not "every example passes".
export const stopText = (s, total) => (s === 'examples pass' && total === 0 ? 'nothing to check' : STOPS[s] || s || '');

export function versionLabel(v, versions) {
  if (!v) return '';
  if (v === 'best') return `best version${versions && versions.best_round ? ` (round ${versions.best_round})` : ''}`;
  if (v === 'latest') return `latest round${versions && versions.latest_round ? ` (round ${versions.latest_round})` : ''}`;
  if (v.startsWith('round:')) return `round ${v.slice(6)}`;
  return { spec: 'specification', baseline: 'baseline (code folder at the start)', snapshot: 'working snapshot (last version judged)', delivered: 'delivered commit', harness: 'harness state' }[v] || v;
}

export function codeView(text, path, { wrap = false, maxLines = 20000 } = {}) {
  const lines = tokenize(text.length > 2_000_000 ? text.slice(0, 2_000_000) : text, langOf(path));
  const body = h('tbody');
  lines.slice(0, maxLines).forEach((toks, i) => {
    body.appendChild(h('tr', null, h('td', { class: 'ln' }, String(i + 1)), h('td', { class: 'lc' }, toks.length ? toks.map(([c, t]) => c ? h('span', { class: c }, t) : t) : '​')));
  });
  const el = h('div', { class: ['codeview', wrap ? 'wrap' : ''] }, h('table', null, body));
  if (lines.length > maxLines) el.appendChild(h('div', { class: 'note' }, `${lines.length - maxLines} more lines not shown`));
  return el;
}

export function diffView(diffText) {
  const body = h('tbody'); let a = 0, b = 0;
  for (const ln of (diffText || '').split('\n')) {
    let cls = '', na = '', nb = '';
    if (ln.startsWith('@@')) { const m = /@@ -(\d+)(?:,\d+)? \+(\d+)/.exec(ln); if (m) { a = +m[1]; b = +m[2]; } cls = 'hunk'; }
    else if (ln.startsWith('+++') || ln.startsWith('---')) cls = 'hunk';
    else if (ln.startsWith('+')) { cls = 'add'; nb = b++; }
    else if (ln.startsWith('-')) { cls = 'del'; na = a++; }
    else if (ln) { na = a++; nb = b++; }
    body.appendChild(h('tr', { class: cls }, h('td', { class: 'ln' }, String(na)), h('td', { class: 'ln' }, String(nb)), h('td', { class: 'lc' }, ln || '​')));
  }
  return h('div', { class: 'codeview diff' }, h('table', null, body));
}

export function exampleText(a) {
  if (!a) return '';
  if (a.type === 'call') return `${a.module}.${a.func}(${(a.args || []).map(x => JSON.stringify(x)).join(', ')})`;
  if (a.type === 'run') return (a.argv || []).join(' ') + (a.stdin != null ? `  ⟵ stdin ${JSON.stringify(a.stdin)}` : '');
  return `${a.type} ${a.path || ''}`;
}
export function expectText(a) {
  if (!a) return '';
  const parts = [];
  if (a.type === 'call') parts.push('raises' in a ? `raises ${a.raises}` : `→ ${JSON.stringify(a.expect)}`);
  if ('stdout' in a) parts.push(a.stdout === '' ? 'prints nothing' : `prints ${JSON.stringify(a.stdout)}`);
  if (a.stdout_contains) parts.push(`output contains ${JSON.stringify(a.stdout_contains)}`);
  if (a.stderr_contains) parts.push(`stderr contains ${JSON.stringify(a.stderr_contains)}`);
  if ('exit' in a && a.type === 'run') parts.push(`exit ${a.exit}`);
  return parts.join(' · ');
}

export function examplesTable(rows) {
  if (!rows || !rows.length) return h('div', { class: 'empty' }, 'No runnable examples were recognised in the spec.');
  return h('table', { class: 't ex' }, h('thead', null, h('tr', null, h('th', null, '#'), h('th', null, 'result'), h('th', null, 'example'), h('th', null, 'expected'), h('th', null, 'what happened'))),
    h('tbody', null, rows.map(r => h('tr', null, h('td', { class: 'num' }, String(r.n)),
      h('td', { class: 'st' }, r.passed === true ? h('span', { class: 'chip ok' }, 'pass') : r.passed === false ? h('span', { class: 'chip bad' }, 'fail') : h('span', { class: 'chip' }, 'not run')),
      h('td', null, h('code', null, exampleText(r.example))), h('td', null, h('code', null, expectText(r.example))), h('td', { class: 'small' }, r.message || '')))));
}

// a small score history: examples passed per round (dots), best so far (line), draft boundaries (ticks)
export function scoreChart(rows, total, { height = 120, onPick } = {}) {
  const W = 600, H = height, pad = 22, n = Math.max(rows.length, 1), max = Math.max(total || 1, 1);
  const x = i => pad + (W - pad * 2) * (n === 1 ? 0.5 : i / (n - 1)), y = v => H - 16 - (H - 30) * (v / max);
  const g = svg('svg', { viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: 'none', role: 'img', 'aria-label': 'examples passed per round and the best so far' });
  g.appendChild(svg('line', { x1: pad, x2: W - pad, y1: y(max), y2: y(max), stroke: 'var(--divider)', 'stroke-dasharray': '3 3' }));
  g.appendChild(svg('line', { x1: pad, x2: W - pad, y1: y(0), y2: y(0), stroke: 'var(--divider)' }));
  g.appendChild(svg('text', { x: 2, y: y(max) + 4, 'font-size': 10, fill: 'var(--text2)' }, String(max)));
  g.appendChild(svg('text', { x: 2, y: y(0) + 4, 'font-size': 10, fill: 'var(--text2)' }, '0'));
  let best = -1, pts = [], prevDraft = null;
  rows.forEach((r, i) => {
    if (prevDraft !== null && r.draft !== prevDraft) g.appendChild(svg('line', { x1: x(i) - (W - pad * 2) / Math.max(n - 1, 1) / 2, x2: x(i) - (W - pad * 2) / Math.max(n - 1, 1) / 2, y1: 6, y2: H - 14, stroke: 'var(--divider)' }));
    prevDraft = r.draft;
    const v = r.built ? (r.examples || 0) : null;
    if (v !== null && v > best) best = v;
    pts.push([x(i), y(Math.max(best, 0))]);
    const dot = v === null ? svg('text', { x: x(i) - 3, y: y(0) + 4, 'font-size': 10, fill: 'var(--red)' }, '×')
                          : svg('circle', { cx: x(i), cy: y(v), r: 3.5, fill: r.improved ? 'var(--green)' : 'var(--content)', stroke: 'var(--green)', 'stroke-width': 1.5 });
    dot.appendChild(svg('title', {}, `round ${r.round}: ${v === null ? 'did not build' : v + ' of ' + total + ' examples'}`));
    if (onPick) { dot.style.cursor = 'pointer'; dot.addEventListener('click', () => onPick(r.round)); }
    g.appendChild(dot);
  });
  if (pts.length > 1) g.insertBefore(svg('polyline', { points: pts.map(p => p.join(',')).join(' '), fill: 'none', stroke: 'var(--orange)', 'stroke-width': 1.5 }), g.firstChild);
  return h('div', { class: 'chart' }, g, h('div', { class: 'legend' },
    h('span', null, h('span', { class: 'sw', style: { background: 'var(--green)' } }), 'examples passed (filled = new best)'),
    h('span', null, h('span', { class: 'sw', style: { background: 'var(--orange)' } }), 'best so far'),
    h('span', null, h('span', { style: { color: 'var(--red)' } }, '× '), 'did not build'), h('span', null, '│ new draft')));
}
