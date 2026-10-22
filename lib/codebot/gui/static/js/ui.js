// Small UI pieces: dialogs, toasts, tabs, resizable panes, an auto-scrolling viewer with "Jump to live", polling that pauses in a hidden tab.
import { h, clear, replace } from './dom.js';

const dlg = () => document.getElementById('dialog');

export function confirmDialog({ title, body, confirm = 'Confirm', danger = false, cancel = 'Cancel' }) {
  return new Promise(resolve => {
    const d = dlg();
    const done = v => { d.close(); resolve(v); };
    replace(d,
      h('div', { class: 'dh' }, title),
      h('div', { class: 'db' }, body),
      h('div', { class: 'df' },
        h('button', { type: 'button', on: { click: () => done(false) } }, cancel),
        h('button', { type: 'button', class: danger ? 'danger' : 'primary', on: { click: () => done(true) } }, confirm)));
    d.onclose = () => resolve(false);
    d.showModal(); d.querySelector('.df button:last-child').focus();
  });
}

// fields: [{name, label, value, type, hint, options}] -> resolves to {name: value} or null
export function formDialog({ title, intro, fields, submit = 'OK', danger = false, validate }) {
  return new Promise(resolve => {
    const d = dlg(); const inputs = {}; const err = h('div', { class: 'field-err' });
    const rows = fields.map(f => {
      let input;
      if (f.type === 'select') input = h('select', { name: f.name }, (f.options || []).map(o => h('option', { value: o.value, selected: o.value === f.value }, o.label)));
      else if (f.type === 'checkbox') input = h('input', { type: 'checkbox', name: f.name, checked: !!f.value });
      else if (f.type === 'textarea') input = h('textarea', { name: f.name, rows: f.rows || 4 }, f.value || '');
      else input = h('input', { type: f.type || 'text', name: f.name, value: f.value ?? '', step: f.step, min: f.min, max: f.max });
      inputs[f.name] = input;
      return f.type === 'checkbox' ? h('label', { class: 'chk' }, input, f.label) : h('label', { class: 'f' }, h('span', null, f.label), input, f.hint ? h('span', { class: 'muted small' }, f.hint) : null);
    });
    const collect = () => Object.fromEntries(Object.entries(inputs).map(([k, el]) => [k, el.type === 'checkbox' ? el.checked : el.value]));
    const form = h('form', { method: 'dialog', class: 'stack', on: { submit: e => {
      e.preventDefault(); const v = collect(); const m = validate && validate(v);
      if (m) { err.textContent = m; return; } d.close(); resolve(v); } } },
      intro ? h('div', null, intro) : null, rows, err);
    replace(d, h('div', { class: 'dh' }, title), h('div', { class: 'db' }, form),
      h('div', { class: 'df' }, h('button', { type: 'button', on: { click: () => { d.close(); resolve(null); } } }, 'Cancel'),
        h('button', { type: 'button', class: danger ? 'danger' : 'primary', on: { click: () => form.requestSubmit() } }, submit)));
    d.onclose = () => resolve(null);
    d.showModal(); const first = d.querySelector('input,select,textarea'); if (first) first.focus();
  });
}

export function toast(msg, kind = '') {
  const t = h('div', { class: ['toast', kind] }, msg);
  document.getElementById('toasts').appendChild(t);
  setTimeout(() => t.remove(), kind === 'bad' ? 9000 : 5000);
}

// tabs([{id, label}], active, onChange) -> {el, set(id)}
export function tabs(items, active, onChange) {
  const el = h('div', { class: 'tabs', role: 'tablist' });
  const set = id => { for (const b of el.children) b.classList.toggle('on', b.dataset.id === id); };
  for (const it of items) el.appendChild(h('button', { type: 'button', role: 'tab', dataset: { id: it.id }, class: it.id === active ? 'on' : '', title: it.title,
    on: { click: () => { set(it.id); onChange(it.id); } } }, it.label));
  return { el, set };
}

// a draggable divider between two panes; widths are remembered per key
export function resizer(left, key, { min = 140, max = 900, measure = 'width' } = {}) {
  const r = h('div', { class: 'resizer', role: 'separator', 'aria-orientation': 'vertical', tabindex: 0, title: 'Drag to resize (arrow keys work too)' });
  const saved = Number(localStorage.getItem('codebot.pane.' + key));
  if (saved) left.style.flex = `0 0 ${saved}px`;
  const apply = w => { w = Math.max(min, Math.min(max, w)); left.style.flex = `0 0 ${w}px`; try { localStorage.setItem('codebot.pane.' + key, String(Math.round(w))); } catch (e) {} };
  r.addEventListener('pointerdown', e => {
    e.preventDefault(); r.setPointerCapture(e.pointerId); r.classList.add('drag');
    const x0 = e.clientX, w0 = left.getBoundingClientRect().width;
    const move = ev => apply(w0 + ev.clientX - x0);
    const up = () => { r.classList.remove('drag'); r.removeEventListener('pointermove', move); r.removeEventListener('pointerup', up); };
    r.addEventListener('pointermove', move); r.addEventListener('pointerup', up);
  });
  r.addEventListener('keydown', e => {
    if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') { e.preventDefault(); apply(left.getBoundingClientRect().width + (e.key === 'ArrowLeft' ? -24 : 24)); }
  });
  return r;
}

// keeps a scroll container pinned to the bottom until the user scrolls up; then offers "Jump to live"
export function follow(scroller, label = 'Jump to live') {
  let pinned = true;
  const btn = h('button', { type: 'button', class: 'jumplive primary', hidden: true, on: { click: () => { pinned = true; btn.hidden = true; scroller.scrollTop = scroller.scrollHeight; } } }, '↓ ' + label);
  scroller.addEventListener('scroll', () => {
    const atBottom = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 30;
    pinned = atBottom; btn.hidden = atBottom;
  });
  return { btn, after() { if (pinned) scroller.scrollTop = scroller.scrollHeight; else btn.hidden = false; }, get pinned() { return pinned; } };
}

// run fn every ms while the page is visible; returns stop()
export function every(ms, fn) {
  let stopped = false, timer = null;
  const tick = async () => {
    if (stopped) return;
    if (!document.hidden) { try { await fn(); } catch (e) { /* shown by the caller */ } }
    if (!stopped) timer = setTimeout(tick, ms);
  };
  tick();
  return () => { stopped = true; clearTimeout(timer); };
}

export function errorBox(e) { return h('div', { class: 'note bad' }, e && e.message ? e.message : String(e)); }
export function loading(el, what = 'Loading') { replace(el, h('div', { class: 'empty' }, what + '…')); }
