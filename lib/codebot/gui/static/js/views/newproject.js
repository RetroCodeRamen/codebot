// New drafts project: name, spec (a file or written here, with a live coverage check), code folder, optional settings, create (and start).
import { h, replace, clear } from '../dom.js';
import { get, post } from '../api.js';
import { toast, errorBox } from '../ui.js';
import { exampleText, expectText } from '../components.js';
import { start } from './conversation.js';

const HELPERS = [
  ['function returns', '`f(1)` is `2`', 'A call of a function the spec declares; also "returns", "gives", "equals"'],
  ['raises', '`f("")` raises `ValueError`', ''],
  ['declare', '`mod.py` provides `f(x)`', 'Says where a function lives (needed for call examples when the spec names several modules)'],
  ['command prints', '`prog arg` prints `out`', 'A command line and its standard output'],
  ['error exit', '`prog bad` prints `error: ...` to standard error, nothing to standard output, and exits with status 1', ''],
  ['error starts with', '`prog bad` prints a message starting with `error:` to standard error and exits with status 2', '"starting with" is checked as "contains"'],
  ['standard input', '`prog` with standard input `line 1\\nline 2` prints `out 1\\nout 2` and exits with status 0', '\\n is a line break'],
];

function specForm(root, parts, params, app) {
  const st = { mode: 'write', cov: null, covErr: null, timer: null, busy: false };
  const name = h('input', { type: 'text', placeholder: 'e.g. calculator', autocomplete: 'off' });
  const specPath = h('input', { type: 'text', placeholder: '/path/to/SPEC.md', style: { width: '100%' } });
  const specText = h('textarea', { rows: 22, spellcheck: false, placeholder: '# What to build\n\nOne paragraph saying what it is and how it is built.\n\n## Requirements\n\n1. ... `prog 2 + 3` prints `5`.\n2. ...' });
  const code = h('input', { type: 'text', placeholder: '/home/you/code/calculator', style: { width: '100%' } });
  const minutes = h('input', { type: 'number', min: 1, max: 1440, placeholder: 'inherit' }), seed = h('input', { type: 'number', min: 0, placeholder: 'inherit' }), model = h('input', { type: 'text', placeholder: 'inherit' });
  const covBox = h('div'), msg = h('div');
  const specArea = h('div');
  const tabWrite = h('button', { type: 'button', on: { click: () => setMode('write') } }, 'Write it here'), tabFile = h('button', { type: 'button', on: { click: () => setMode('file') } }, 'Use a file');

  function setMode(m) { st.mode = m; tabWrite.classList.toggle('primary', m === 'write'); tabFile.classList.toggle('primary', m === 'file'); renderSpecArea(); schedule(); }
  function renderSpecArea() {
    if (st.mode === 'file') replace(specArea, h('label', { class: 'f' }, h('span', null, 'Spec file (it is copied into the project when it is created)'), specPath));
    else replace(specArea, h('div', { class: 'row', style: { marginBottom: '6px' } }, h('span', { class: 'muted small' }, 'insert an example:'),
      HELPERS.map(([lab, txt, note]) => h('button', { type: 'button', class: 'small', title: note || txt, on: { click: () => insert(txt) } }, lab))), specText);
  }
  function insert(txt) {
    const a = specText.selectionStart ?? specText.value.length, b = specText.selectionEnd ?? a;
    specText.value = specText.value.slice(0, a) + txt + specText.value.slice(b); specText.focus(); specText.selectionStart = specText.selectionEnd = a + txt.length; schedule();
  }
  function schedule() { clearTimeout(st.timer); st.timer = setTimeout(check, 600); }
  async function check() {
    const body = st.mode === 'file' ? { path: specPath.value.trim() } : { text: specText.value };
    if (!(body.path || (body.text || '').trim())) { st.cov = null; st.covErr = null; renderCov(); return; }
    if (code.value.trim()) body.code_dir = code.value.trim();
    try { st.cov = await post('/api/check-spec', body); st.covErr = null; } catch (e) { st.cov = null; st.covErr = e.message; }
    renderCov();
  }
  function renderCov() {
    if (st.covErr) return replace(covBox, h('div', { class: 'note bad' }, st.covErr));
    const r = st.cov; if (!r) return replace(covBox, h('div', { class: 'muted' }, 'The coverage check runs as you type: which requirements have examples codebot can run.'));
    const c = r.coverage, unc = c.uncovered || [];
    replace(covBox,
      h('div', { class: 'row' }, h('span', { class: 'chip plain' }, `${c.requirements.length} requirements`), h('span', { class: ['chip', c.examples ? 'ok' : 'bad'] }, `${c.examples} runnable examples`),
        c.declared && c.declared.length ? h('span', { class: 'chip' }, `${c.declared.length} declared names checked`) : null, h('span', { class: 'chip' }, c.language || '?'),
        c.build ? h('span', { class: 'chip mono' }, c.build) : null, r.fits ? null : h('span', { class: 'chip bad' }, 'too large for drafts mode')),
      !c.examples ? h('div', { class: 'note bad' }, 'No runnable examples at all: a run can only check that the code builds, and it stops at the first version that does.') : null,
      unc.length ? h('div', { class: 'note warn' }, h('b', null, `Nothing will check requirement${unc.length > 1 ? 's' : ''} ${unc.join(', ')}`), ': they have no runnable example. Add some, or accept that only the others are checked.') : null,
      r.why ? h('div', { class: 'note bad' }, r.why) : null,
      h('table', { class: 't', style: { marginTop: '8px' } }, h('thead', null, h('tr', null, h('th', null, '#'), h('th', null, 'requirement'), h('th', null, 'examples'))),
        h('tbody', null, c.requirements.map(q => h('tr', null, h('td', { class: 'num' }, String(q.n)), h('td', { class: 'small' }, q.title),
          h('td', null, q.examples ? h('span', { class: 'chip ok' }, String(q.examples)) : h('span', { class: 'chip bad' }, q.names && q.names.length ? `none (only that ${q.names.join(', ')} exist)` : 'none')))))),
      r.examples.length ? h('details', { style: { marginTop: '8px' } }, h('summary', null, `The ${r.examples.length} checks codebot will run`),
        h('table', { class: 't small' }, h('tbody', null, r.examples.map((a, i) => h('tr', null, h('td', { class: 'num' }, String(i + 1)), h('td', null, h('code', null, exampleText(a))), h('td', null, h('code', null, expectText(a)))))))) : null,
      h('p', { class: 'muted small' }, 'Passing these examples is not proof of every requirement: only the examples are checked.'));
  }
  async function create(start) {
    clear(msg);
    const body = { name: name.value.trim(), code_dir: code.value.trim(), start, overrides: {} };
    if (st.mode === 'file') body.spec_path = specPath.value.trim(); else body.spec_text = specText.value;
    if (minutes.value) body.overrides.DRAFTS_MINUTES = minutes.value; if (seed.value) body.overrides.DRAFTS_SEED = seed.value; if (model.value.trim()) body.overrides.CODEBOT_MODEL = model.value.trim();
    if (!/^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(body.name)) return replace(msg, h('div', { class: 'note bad' }, 'A project name is letters, digits, ".", "_" and "-", starting with a letter or digit.'));
    st.busy = true;
    try {
      const r = await post('/api/projects/new', body);
      if (!r.ok) { replace(msg, h('div', { class: 'note bad' }, h('pre', { class: 'textblock' }, r.out || 'not created'))); st.busy = false; return; }
      toast(`Created ${body.name}` + (r.start ? (r.start.ok ? ' and started it' : `; start failed: ${r.start.out}`) : ''), r.start && !r.start.ok ? 'warn' : '');
      location.hash = `#/p/${encodeURIComponent(body.name)}`;
    } catch (e) { replace(msg, errorBox(e)); }
    st.busy = false;
  }
  const job = () => (app.status || {}).job;
  const startBtn = h('button', { type: 'button', class: 'primary', on: { click: () => create(true) } }, 'Create and start');
  const updateStart = () => { const j = job(); startBtn.disabled = !!j; startBtn.title = j ? `The model is busy (${j.kind} ${j.name || ''}); create now and start it later` : 'Start the run in the background right away'; };
  app.onStatus.add(updateStart); updateStart();

  for (const el of [specText, specPath, code]) el.addEventListener('input', schedule);
  root.appendChild(h('div', { class: 'page' },
    h('div', { class: 'pagehead' }, h('h1', null, 'Start from a specification'), h('a', { href: '#/new' }, 'Describe it in your own words instead')),
    h('div', { class: 'grid2' },
      h('div', { class: 'stack' },
        h('div', { class: 'panel' }, h('div', { class: 'pb stack' },
          h('label', { class: 'f' }, h('span', null, '1. Project name'), name),
          h('div', null, h('div', { class: 'row', style: { marginBottom: '6px' } }, h('span', null, '2. Specification'), h('span', { class: 'spacer' }), tabWrite, tabFile), specArea))),
        h('div', { class: 'panel' }, h('div', { class: 'pb stack' },
          h('label', { class: 'f' }, h('span', null, '3. Code folder (outside codebot\'s own folder; created if missing)'), code),
          h('div', { class: 'note' }, 'If the folder is a git repository with history, codebot works in its own worktree on branch ', h('code', null, 'codebot/<name>'), ' and never touches your checkout. Otherwise it initialises git there and works on that branch in place. Every new best version is committed; you merge when you are happy.'),
          h('details', null, h('summary', null, '4. Optional settings for this project (empty = inherit)'),
            h('div', { class: 'grid3', style: { marginTop: '8px' } }, h('label', { class: 'f' }, h('span', null, 'time budget, minutes'), minutes), h('label', { class: 'f' }, h('span', null, 'seed'), seed), h('label', { class: 'f' }, h('span', null, 'model'), model)),
            h('p', { class: 'muted small' }, 'The time budget (DRAFTS_MINUTES) is the whole run\'s model and judging time; the run stops sooner when every example passes.')),
          h('div', { class: 'row' }, h('button', { type: 'button', on: { click: () => create(false) } }, 'Create'), startBtn), msg))),
      h('div', { class: 'panel' }, h('div', { class: 'ph' }, h('h2', null, 'Coverage'), h('span', { class: 'spacer' }), h('button', { type: 'button', on: { click: check } }, 'Check now')), h('div', { class: 'pb' }, covBox)))));
  setMode('write'); renderCov();
  if (params.from) (async () => {
    try { const d = await get(`/api/project/${encodeURIComponent(params.from)}`); specText.value = d.spec || ''; code.value = d.state.workdir || ''; name.value = params.from.replace(/-(\d+)$/, '') + '-edit'; schedule(); }
    catch (e) { toast(e.message, 'bad'); }
  })();
  return () => app.onStatus.delete(updateStart);
}

export function mount(root, parts, params, app) {
  if (params.spec === '1') return specForm(root, parts, params, app);
  return start(root, app);
}
