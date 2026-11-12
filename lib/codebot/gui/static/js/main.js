// codebot GUI: theme, router, the global status strip. Views live in views/*.js and return a cleanup function (stops their polling).
import { h, replace } from './dom.js';
import { get, conn } from './api.js';
import { every } from './ui.js';

const views = {
  home: () => import('./views/home.js'), projects: () => import('./views/projects.js'), p: () => import('./views/project.js'), new: () => import('./views/newproject.js'),
  bench: () => import('./views/bench.js'), models: () => import('./views/models.js'), machine: () => import('./views/machine.js'),
  inbox: () => import('./views/home.js'), // legacy URL → Projects library
  settings: () => import('./views/settings.js'),
  pipeline: () => import('./views/pipeline.js'),
};
export const app = { status: null, onStatus: new Set() };

// ---------- appearance: follow the system unless the user picked light or dark ----------
const THEMES = ['system', 'light', 'dark'];
function applyTheme(t) {
  if (t === 'light' || t === 'dark') document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme;
  const b = document.getElementById('theme-btn'); b.title = `Appearance: ${t === 'system' ? 'follow the system' : t} (click to change)`; b.textContent = t === 'light' ? '☀' : t === 'dark' ? '☾' : '◐';
}
let theme = 'system'; try { theme = localStorage.getItem('codebot.theme') || 'system'; } catch (e) {}
applyTheme(theme);
document.getElementById('theme-btn').addEventListener('click', () => {
  theme = THEMES[(THEMES.indexOf(theme) + 1) % 3]; applyTheme(theme); try { localStorage.setItem('codebot.theme', theme); } catch (e) {}
});

// ---------- router ----------
let cleanup = null, seq = 0;
function setChrome(key, params) {
  // Progressive disclosure: human screens hide machine telemetry extras.
  const tools = ['bench', 'models', 'machine', 'settings'];
  const workshop = key === 'p' && params.workshop === '1';
  const chrome = tools.includes(key) || workshop ? (tools.includes(key) ? 'tools' : 'workshop') : 'human';
  document.body.dataset.chrome = chrome;
}

async function route() {
  const hash = location.hash || '#/projects';
  const [path, query] = hash.slice(2).split('?');
  const parts = path.split('/').map(decodeURIComponent); const key = parts[0] || 'projects';
  const params = Object.fromEntries(new URLSearchParams(query || ''));
  const top = key === 'p' || key === 'home' || key === 'inbox' ? 'projects' : key;
  setChrome(key, params);
  for (const a of document.querySelectorAll('[data-nav]')) a.classList.toggle('on', a.dataset.nav === top);
  const adv = document.getElementById('adv-btn'); if (adv) adv.classList.toggle('on', ['bench', 'models', 'machine', 'settings'].includes(key));
  const pop = document.getElementById('adv-pop'); if (pop) { pop.hidden = true; adv && adv.setAttribute('aria-expanded', 'false'); }
  if (cleanup) { try { cleanup(); } catch (e) {} cleanup = null; }
  const mine = ++seq; const el = document.getElementById('view');
  const load = views[key];
  if (!load) { replace(el, h('div', { class: 'page' }, h('div', { class: 'empty' }, 'No such page. ', h('a', { href: '#/projects' }, 'Projects')))); return; }
  try {
    const mod = await load(); if (mine !== seq) return;
    replace(el); cleanup = mod.mount(el, parts.slice(1), params, app) || null;
  } catch (e) { replace(el, h('div', { class: 'page' }, h('div', { class: 'note bad' }, 'This page failed to load: ' + e.message))); }
}
window.addEventListener('hashchange', route);
const advBtn = document.getElementById('adv-btn'), advPop = document.getElementById('adv-pop');
if (advBtn && advPop) {
  advBtn.addEventListener('click', e => { e.stopPropagation(); advPop.hidden = !advPop.hidden; advBtn.setAttribute('aria-expanded', advPop.hidden ? 'false' : 'true'); });
  advPop.addEventListener('click', () => { advPop.hidden = true; advBtn.setAttribute('aria-expanded', 'false'); });
  document.addEventListener('click', () => { advPop.hidden = true; advBtn.setAttribute('aria-expanded', 'false'); });
}

// ---------- global status strip (bottom) + contextual banners ----------
const strip = document.getElementById('statusstrip'), banner = document.getElementById('banner');
let offline = false;
const it = (...c) => h('span', { class: 'it' }, ...c);
function updateBanner(s) {
  if (!s) return;
  if (s.demo) {
    banner.hidden = false; banner.className = 'banner demo';
    banner.textContent = 'Demo — the projects here are a sample, and buttons do nothing.';
    return;
  }
  if (offline) {
    banner.hidden = false; banner.className = 'banner';
    banner.textContent = 'Lost the connection to the codebot GUI server. Runs are not affected; reconnecting…';
    return;
  }
  const o = s.ollama || {};
  if (!o.up && document.body.dataset.chrome === 'human') {
    banner.hidden = false; banner.className = 'banner';
    banner.textContent = 'The model server is off — open Tools → Models, or run codebot doctor.';
    return;
  }
  banner.hidden = true; banner.className = 'banner'; banner.textContent = '';
}
function renderStrip(s) {
  const fresh = Date.now() / 1000 - (s.time || 0) < 15;
  const o = s.ollama || {}; const m = s.machine || {};
  const t = m.temp; const tmax = Number(s.thermal && s.thermal.max) || 92;
  const job = s.job; let jobEl;
  const word = { project: 'Building', benchmark: 'A benchmark is running', preflight: 'Checking the model', eval: 'Trying a model', thermal: 'Measuring heat', planning: 'Reading a design', tidy: 'Tidying notes' };
  if (job) {
    const href = job.kind === 'project' || job.kind === 'planning' ? `#/p/${encodeURIComponent(job.name)}` : job.kind === 'benchmark' ? `#/bench/${encodeURIComponent(job.name)}` : job.kind === 'preflight' ? '#/models' : '#/machine';
    const label = job.kind === 'project' || job.kind === 'planning' ? `${word[job.kind] || job.kind} ${job.name || ''}`.trim() : (word[job.kind] || job.kind);
    jobEl = it(h('span', { class: 'dot warn' }), h('a', { href, title: s.job_text || '' }, label));
  } else jobEl = it(h('span', { class: 'dot off' }), 'Idle');
  replace(strip,
    it(h('span', { class: ['dot', o.up ? 'ok' : 'bad'] }), o.up ? 'Ready' : h('span', { title: o.why || '' }, 'Model server is off')),
    jobEl,
    t == null ? null : h('span', { class: 'it extra' }, h('span', { class: ['dot', t >= tmax ? 'bad' : t >= tmax - 6 ? 'warn' : 'ok'] }), `${Math.round(t)}°`),
    s.notifications && s.notifications.phone_control ? h('span', { class: 'it extra' }, h('b', { title: 'Anyone with the topic can pause, resume, stop, and answer' }, 'Phone control is on')) : null,
    fresh ? null : it(h('b', { class: 'stale' }, 'Reconnecting')));
  updateBanner(s);
}
conn.on(online => { offline = !online; updateBanner(app.status || {}); });
every(3000, async () => {
  try { const s = await get('/api/status'); app.status = s; renderStrip(s); app.onStatus.forEach(f => f(s)); }
  catch (e) { if (app.status) renderStrip({ ...app.status }); }
});
route();
