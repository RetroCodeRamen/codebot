// Machine: temperature and the thermal guard, memory and swap traffic, GPU, disk, sandbox, watchdog, doctor, and the GUI's long actions.
import { h, svg, replace, bytes, when, clock } from '../dom.js';
import { get, post } from '../api.js';
import { every, errorBox, toast } from '../ui.js';

export function actionLog(a) {
  const out = h('pre', { class: 'textblock', style: { maxHeight: '260px', overflow: 'auto' } }, '…');
  const st = h('span', { class: ['chip', a.running ? 'act' : a.exit === 0 ? 'ok' : 'bad'] }, a.running ? 'running' : a.lost ? 'ended (outcome unknown)' : `exit ${a.exit}`);
  const box = h('details', { open: a.running }, h('summary', null, h('b', null, a.label), ' ', st, ' ', h('span', { class: 'muted small' }, when(a.started))), out);
  box.addEventListener('toggle', async () => { if (box.open) try { const r = await get(`/api/action/${encodeURIComponent(a.id)}`); out.textContent = r.text || '(no output yet)'; } catch (e) { out.textContent = e.message; } });
  if (a.running) get(`/api/action/${encodeURIComponent(a.id)}`).then(r => { out.textContent = r.text || '(no output yet)'; }).catch(() => {});
  return box;
}

function chart(rows, key, { max, min = 0, color = 'var(--green)', fmt = v => v, lines = [] }) {
  const W = 600, H = 140, pad = 26; const pts = rows.filter(r => r[key] != null);
  const g = svg('svg', { viewBox: `0 0 ${W} ${H}`, class: 'spark', preserveAspectRatio: 'none', role: 'img' });
  if (!pts.length) { g.appendChild(svg('text', { x: W / 2 - 40, y: H / 2, fill: 'var(--text2)', 'font-size': 12 }, 'no readings yet')); return g; }
  const t0 = pts[0].t, t1 = pts[pts.length - 1].t || t0 + 1; const top = max ?? (Math.max(...pts.map(p => p[key])) * 1.1 || 1);
  const x = t => pad + (W - pad - 8) * ((t - t0) / Math.max(t1 - t0, 1)), y = v => H - 18 - (H - 30) * ((v - min) / Math.max(top - min, 1e-9));
  for (const [v, label, c] of lines) { g.appendChild(svg('line', { x1: pad, x2: W - 8, y1: y(v), y2: y(v), stroke: c, 'stroke-dasharray': '4 3' })); g.appendChild(svg('text', { x: 2, y: y(v) + 4, 'font-size': 10, fill: c }, label)); }
  g.appendChild(svg('polyline', { points: pts.map(p => `${x(p.t)},${y(p[key])}`).join(' '), fill: 'none', stroke: color, 'stroke-width': 1.5 }));
  g.appendChild(svg('text', { x: pad, y: H - 4, 'font-size': 10, fill: 'var(--text2)' }, clock(t0)));
  g.appendChild(svg('text', { x: W - 50, y: H - 4, 'font-size': 10, fill: 'var(--text2)' }, clock(t1)));
  g.appendChild(svg('text', { x: 2, y: 12, 'font-size': 10, fill: 'var(--text2)' }, fmt(top)));
  return g;
}

export function mount(root, parts, params, app) {
  const page = h('div', { class: 'page' }); root.appendChild(page);
  let refreshDisk = false;
  const draw = async () => {
    try {
      const r = await get(`/api/machine?seconds=3600${refreshDisk ? '&refresh_disk=1' : ''}`); refreshDisk = false;
      const acts = (await get('/api/actions')).actions;
      const th = r.thermal, l = r.latest || {}, m = r.mem || {}, g = r.gpu || {}, sb = r.sandbox || {}, wd = r.watchdog || {}, dk = r.disk;
      const usedPct = m.total ? (1 - m.available / m.total) : 0, swapPct = m.swap_total ? (1 - m.swap_free / m.swap_total) : 0;
      replace(page, h('div', { class: 'pagehead' }, h('h1', null, 'Machine'), h('span', { class: 'muted' }, 'readings from this laptop; the history covers the time the GUI has been running'), h('span', { class: 'grow' }),
        h('button', { type: 'button', on: { click: doctor } }, 'Run doctor')),
        h('div', { class: 'grid2' },
          h('div', { class: 'panel' }, h('div', { class: 'ph' }, h('h2', null, 'CPU temperature'), h('span', { class: 'spacer' }), h('span', { class: 'mono' }, l.temp != null ? `${Math.round(l.temp)}°C` : 'no reading')),
            h('div', { class: 'pb' }, chart(r.history, 'temp', { max: 100, min: 30, color: 'var(--orange)', fmt: v => `${v}°`, lines: [[+th.max, `${th.max}°`, 'var(--red)'], [+th.resume, `${th.resume}°`, 'var(--green)']] }),
              h('p', { class: 'small' }, th.guard !== '0' ? `Thermal guard on: model calls wait at ${th.max}°C or above until the CPU is back at ${th.resume}°C (at most ${th.max_wait} s per wait). A run cooling down shows as "cooling" and cannot be resumed early.` : h('span', { class: 'stale' }, 'Thermal guard off: model calls never wait for the CPU to cool.')))),
          h('div', { class: 'panel' }, h('div', { class: 'ph' }, h('h2', null, 'Memory and swap')), h('div', { class: 'pb' },
            h('dl', { class: 'kv' }, h('dt', null, 'RAM free'), h('dd', null, `${bytes(m.available)} of ${bytes(m.total)}`, h('div', { class: ['bar', usedPct > .9 ? 'bad' : usedPct > .75 ? 'warn' : ''] }, h('i', { style: { width: `${Math.round(usedPct * 100)}%` } }))),
              h('dt', null, 'swap used'), h('dd', null, `${bytes(m.swap_total - m.swap_free)} of ${bytes(m.swap_total)}`, h('div', { class: ['bar', swapPct > .85 ? 'bad' : swapPct > .6 ? 'warn' : ''] }, h('i', { style: { width: `${Math.round(swapPct * 100)}%` } }))),
              h('dt', null, 'swap traffic'), h('dd', null, l.swap_in_bps == null ? 'measuring…' : `${bytes(l.swap_in_bps)}/s in, ${bytes(l.swap_out_bps)}/s out`)),
            chart(r.history, 'mem_available', { color: 'var(--green)', fmt: v => bytes(v) }),
            h('p', { class: 'muted small' }, 'A large model can push your other programs into swap while it runs (see docs/LARGE_MODELS.md).')))),
        h('div', { class: 'grid3', style: { marginTop: '14px' } },
          h('div', { class: 'panel' }, h('div', { class: 'ph' }, h('h2', null, 'GPU')), h('div', { class: 'pb' }, g.available ? h('dl', { class: 'kv' }, h('dt', null, 'card'), h('dd', null, g.name), h('dt', null, 'memory'), h('dd', null, `${bytes(g.mem_used)} of ${bytes(g.mem_total)}`), h('dt', null, 'temperature'), h('dd', null, `${g.temp}°C`), h('dt', null, 'busy'), h('dd', null, `${g.util}%`))
            : h('div', { class: 'muted' }, g.why || 'unavailable'))),
          h('div', { class: 'panel' }, h('div', { class: 'ph' }, h('h2', null, 'Sandbox')), h('div', { class: 'pb' }, h('dl', { class: 'kv' }, h('dt', null, 'setting'), h('dd', { class: 'mono' }, sb.mode), h('dt', null, 'docker'), h('dd', null, sb.available ? 'available' : h('span', { class: 'stale' }, sb.why || 'unavailable')),
            sb.available ? [h('dt', null, 'image'), h('dd', null, h('span', { class: 'mono' }, sb.image), ' ', sb.image_present ? h('span', { class: 'chip ok' }, 'present') : h('span', { class: 'chip bad' }, 'missing'))] : null, h('dt', null, 'memory cap'), h('dd', { class: 'mono' }, sb.memory)),
            sb.mode === 'none' ? h('div', { class: 'note warn' }, 'SANDBOX=none: model-written code runs without the container.') : null, sb.mode !== 'none' && !(sb.available && sb.image_present) ? h('div', { class: 'note bad' }, 'The sandbox is not usable: runs that need it refuse to start (docker) or run without it (auto).') : null)),
          h('div', { class: 'panel' }, h('div', { class: 'ph' }, h('h2', null, 'Watchdog')), h('div', { class: 'pb' }, wd.known ? h('dl', { class: 'kv' }, h('dt', null, 'timer'), h('dd', null, `${wd.enabled}, ${wd.active}`)) : h('div', { class: 'muted' }, wd.why || 'unknown'),
            h('p', { class: 'muted small' }, 'Every 5 minutes it restarts project loops that should be running but died, and benchmark runners. Install: ', h('code', null, 'codebot install-service'), '.')))),
        h('div', { class: 'panel', style: { marginTop: '14px' } }, h('div', { class: 'ph' }, h('h2', null, 'Disk'), h('span', { class: 'spacer' }), h('button', { type: 'button', disabled: r.disk_busy, on: { click: () => { refreshDisk = true; draw(); } } }, r.disk_busy ? 'Measuring…' : dk ? 'Measure again' : 'Measure')),
          h('div', { class: 'pb' }, dk ? h('div', null, dk.fs ? h('p', null, `${bytes(dk.fs.free)} free of ${bytes(dk.fs.total)} (measured ${when(dk.at)})`) : null,
            h('table', { class: 't' }, h('tbody', null, Object.entries(dk.dirs || {}).map(([k, v]) => h('tr', null, h('td', { class: 'mono' }, k), h('td', { class: 'num' }, bytes(v))))))) : h('div', { class: 'muted' }, 'Not measured yet (it walks the folders, so it only runs when asked).'),
            h('p', { class: 'muted small' }, 'Cleanup is not offered here yet: deleting projects, work folders or models needs a reviewed backend operation (see docs/dev/GUI_PLAN.md).'))),
        h('div', { class: 'panel', style: { marginTop: '14px' } }, h('div', { class: 'ph' }, h('h2', null, 'Actions started from the GUI')), h('div', { class: 'pb stack' }, acts.length ? acts.map(a => actionLog(a)) : h('div', { class: 'muted' }, 'None yet.'))));
    } catch (e) { replace(page, errorBox(e)); }
  };
  const doctor = async () => { try { await post('/api/machine/doctor', {}); toast('Doctor started'); draw(); } catch (e) { toast(e.message, 'bad'); } };
  return every(10000, draw);
}
