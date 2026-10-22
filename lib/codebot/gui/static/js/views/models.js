// Models: the private model server, its models, the default model and context, preflight, import, unload, delete (with confirmation).
import { h, replace, bytes, when } from '../dom.js';
import { get, post } from '../api.js';
import { every, errorBox, toast, confirmDialog, formDialog } from '../ui.js';
import { actionLog } from './machine.js';

export function mount(root, parts, params, app) {
  const page = h('div', { class: 'page' }); root.appendChild(page);
  const draw = async () => {
    try {
      const r = await get('/api/models'); const s = r.server || {}; const loaded = new Map((s.loaded || []).map(x => [x.name, x]));
      const isDefault = n => n === r.default || n === r.default + ':latest';
      replace(page, h('div', { class: 'pagehead' }, h('h1', null, 'Models'), h('span', { class: 'grow' }), h('button', { type: 'button', on: { click: preflight } }, 'Run the preflight check'), h('button', { type: 'button', on: { click: importModel } }, 'Import a model…')),
        h('div', { class: 'grid2' },
          h('div', { class: 'panel' }, h('div', { class: 'ph' }, h('h2', null, 'Model server')), h('div', { class: 'pb' }, h('dl', { class: 'kv' },
            h('dt', null, 'state'), h('dd', null, s.up ? h('span', { class: 'chip ok' }, `up · ollama ${s.version}`) : h('span', { class: 'chip bad' }, 'down')),
            h('dt', null, 'address'), h('dd', { class: 'mono' }, `127.0.0.1:${s.port || '?'} (private, this machine only)`),
            h('dt', null, 'loaded now'), h('dd', null, (s.loaded || []).length ? s.loaded.map(x => h('div', { class: 'mono small' }, `${x.name} · ${bytes(x.size)} (${bytes(x.vram)} on GPU) · context ${x.ctx || '?'} · unloads ${when(x.expires)}`)) : 'none'),
            h('dt', null, 'model job'), h('dd', null, r.job ? `${r.job.kind} ${r.job.name || ''}` : 'none (a loaded model is not proof of work)')),
            !s.up ? h('div', { class: 'note bad' }, s.why || 'The model server does not answer. ', h('code', null, 'codebot setup'), ' starts it.') : null)),
          h('div', { class: 'panel' }, h('div', { class: 'ph' }, h('h2', null, 'Defaults for codebot\'s engine')), h('div', { class: 'pb' }, h('dl', { class: 'kv' },
            h('dt', null, 'model'), h('dd', { class: 'mono' }, r.default), h('dt', null, 'context'), h('dd', { class: 'mono' }, String(r.num_ctx)), h('dt', null, 'keep loaded'), h('dd', { class: 'mono' }, r.keep_alive || '–')),
            h('p', { class: 'muted small' }, 'Set for this machine (config/local.env). opencode-based paths (the planning chat, legacy projects) keep using codebot-model.')))),
        h('div', { class: 'panel', style: { marginTop: '14px' } }, h('div', { class: 'ph' }, h('h2', null, 'Models on this machine')),
          (r.list || {}).ok ? h('table', { class: 't' }, h('thead', null, h('tr', null, ['name', 'size', 'family', 'parameters', 'quantization', '', ''].map(c => h('th', null, c)))),
            h('tbody', null, r.list.models.map(m => h('tr', null, h('td', { class: 'mono' }, m.name, isDefault(m.name) ? h('span', { class: 'chip ok', style: { marginLeft: '6px' } }, 'default') : null, loaded.has(m.name) ? h('span', { class: 'chip act', style: { marginLeft: '6px' } }, 'loaded') : null),
              h('td', { class: 'num' }, bytes(m.size)), h('td', null, m.family || ''), h('td', null, m.params || ''), h('td', null, m.quant || ''),
              h('td', null, isDefault(m.name) ? null : h('button', { type: 'button', on: { click: () => setDefault(m.name) } }, 'Make default'), loaded.has(m.name) ? h('button', { type: 'button', on: { click: () => unload(m.name) } }, 'Unload') : null),
              h('td', null, h('button', { type: 'button', class: 'danger', disabled: !!m.protected, title: m.protected ? `Not offered: ${m.protected}` : 'Delete this model from disk', on: { click: () => del(m) } }, 'Delete…'))))))
            : h('div', { class: 'pb muted' }, 'The model list is unavailable: the server is down or did not answer.')),
        r.actions.length ? h('div', { class: 'panel', style: { marginTop: '14px' } }, h('div', { class: 'ph' }, h('h2', null, 'Recent checks and imports')), h('div', { class: 'pb' }, r.actions.map(a => actionLog(a)))) : null);
    } catch (e) { replace(page, errorBox(e)); }
  };
  const preflight = async () => {
    const j = (app.status || {}).job; if (j) { toast(`The model is busy: ${j.kind} ${j.name || ''}`, 'bad'); return; }
    try { await post('/api/models/preflight', {}); toast('Preflight started: it loads the model and checks context, keep-alive, JSON schema and cache reuse (about 5 min)'); draw(); } catch (e) { toast(e.message, 'bad'); }
  };
  const setDefault = async name => {
    const j = (app.status || {}).job;
    if (!await confirmDialog({ title: `Make ${name} the default?`, confirm: 'Make default', body: h('div', null, h('p', null, 'Writes CODEBOT_MODEL for this machine (config/local.env). The engine reads it before every round, so a running project switches at its next round.'), j ? h('p', { class: 'note warn' }, `A model job is running (${j.kind} ${j.name || ''}).`) : null, h('p', { class: 'muted' }, 'Check its context size (NUM_CTX) fits: run the preflight afterwards.')) })) return;
    try { await post('/api/models/default', { model: name, confirm: true }); toast(`${name} is the default`); draw(); } catch (e) { toast(e.message, 'bad'); }
  };
  const unload = async name => {
    const j = (app.status || {}).job;
    if (j && !await confirmDialog({ title: `Unload ${name}?`, confirm: 'Unload', body: h('p', null, `A model job is running (${j.kind} ${j.name || ''}); its next model call would load the model again.`) })) return;
    try { const r = await post('/api/models/unload', { model: name, confirm: true }); toast(r.ok ? 'unloaded' : r.out, r.ok ? '' : 'bad'); draw(); } catch (e) { toast(e.message, 'bad'); }
  };
  const del = async m => {
    if (!await confirmDialog({ title: `Delete ${m.name}?`, danger: true, confirm: 'Delete from disk', body: h('div', null, h('p', null, `This removes the model (${bytes(m.size)}) from this machine. Getting it back needs the original file or a download.`), h('p', { class: 'muted' }, h('code', null, `ollama rm ${m.name}`))) })) return;
    try { const r = await post('/api/models/delete', { model: m.name, confirm: true }); toast(r.ok ? `deleted ${m.name}` : r.out, r.ok ? '' : 'bad'); draw(); } catch (e) { toast(e.message, 'bad'); }
  };
  const importModel = async () => {
    const v = await formDialog({ title: 'Import a model', submit: 'Import', intro: h('p', { class: 'muted' }, 'From an Ollama Modelfile (FROM a GGUF file, its template or renderer, parameters). See bench/bigmodel/Modelfile.coder30b-iq3 for an example.'),
      fields: [{ name: 'name', label: 'Model name' }, { name: 'modelfile', label: 'Modelfile path' }] });
    if (!v) return;
    try { await post('/api/models/import', v); toast('Import started; it copies the model file into codebot\'s model store'); draw(); } catch (e) { toast(e.message, 'bad'); }
  };
  return every(5000, draw);
}
