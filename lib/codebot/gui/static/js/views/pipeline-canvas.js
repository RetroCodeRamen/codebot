// SVG team + workflow canvases. Talk links = can_talk_to; positions stored in ui.team.positions.
import { h } from '../dom.js';

const W = 720, H = 420, R = 28;

function layoutMembers(members, ui) {
  const pos = ((ui || {}).team || {}).positions || {};
  const n = Math.max(members.length, 1);
  return members.map((m, i) => {
    if (pos[m.id] && Number.isFinite(pos[m.id].x)) return { id: m.id, x: pos[m.id].x, y: pos[m.id].y, m };
    const a = (Math.PI * 2 * i) / n - Math.PI / 2;
    return { id: m.id, x: W / 2 + Math.cos(a) * 140, y: H / 2 + Math.sin(a) * 110, m };
  });
}

function layoutWf(nodes, ui) {
  const pos = ((ui || {}).workflow || {}).positions || {};
  const cols = Math.max(3, Math.ceil(Math.sqrt(Math.max(nodes.length, 1))));
  return nodes.map((n, i) => {
    if (pos[n.id] && Number.isFinite(pos[n.id].x)) return { id: n.id, x: pos[n.id].x, y: pos[n.id].y, n };
    return { id: n.id, x: 80 + (i % cols) * 160, y: 70 + Math.floor(i / cols) * 100, n };
  });
}

function arrow(x1, y1, x2, y2, cls, inset) {
  const pad = inset || R;
  const dx = x2 - x1, dy = y2 - y1, len = Math.hypot(dx, dy) || 1;
  const ux = dx / len, uy = dy / len;
  const sx = x1 + ux * pad, sy = y1 + uy * pad, ex = x2 - ux * pad, ey = y2 - uy * pad;
  const bx = ex - ux * 8 + uy * 5, by = ey - uy * 8 - ux * 5;
  const cx = ex - ux * 8 - uy * 5, cy = ey - uy * 8 + ux * 5;
  return h('g', { class: cls || 'pipe-edge' },
    h('line', { x1: sx, y1: sy, x2: ex, y2: ey }),
    h('polygon', { points: `${ex},${ey} ${bx},${by} ${cx},${cy}` }));
}

function edgePairs(members) {
  const pairs = [];
  for (const m of members) {
    const seen = new Set();
    for (const link of m.links || []) {
      if (!link || !link.to || seen.has(link.to)) continue;
      seen.add(link.to);
      pairs.push({ from: m.id, to: link.to, kind: link.kind || 'agent' });
    }
    for (const t of m.can_talk_to || []) {
      if (seen.has(t)) continue;
      pairs.push({ from: m.id, to: t, kind: 'agent' });
    }
  }
  return pairs;
}

function redrawEdges(gEdges, members, byId) {
  while (gEdges.firstChild) gEdges.removeChild(gEdges.firstChild);
  for (const e of edgePairs(members)) {
    if (!byId[e.from] || !byId[e.to]) continue;
    const cls = e.kind === 'repository' ? 'pipe-edge dim' : (e.kind === 'both' ? 'pipe-edge both' : 'pipe-edge');
    gEdges.appendChild(arrow(byId[e.from].x, byId[e.from].y, byId[e.to].x, byId[e.to].y, cls));
  }
}

export function teamCanvas({ team, agents, run, ui, selected, onSelect, onToggleLink, onDragEnd }) {
  const members = team.members || [];
  const nodes = layoutMembers(members, ui);
  const byId = Object.fromEntries(nodes.map(n => [n.id, n]));
  const svg = h('svg', { class: 'pipe-canvas', viewBox: `0 0 ${W} ${H}`, width: '100%', height: String(H) });
  const gEdges = h('g', { class: 'pipe-edges' });
  const gNodes = h('g', { class: 'pipe-nodes' });
  redrawEdges(gEdges, members, byId);

  for (const n of nodes) {
    const st = ((run || {}).members || {})[n.id] || {};
    const ag = agents[n.m.agent] || {};
    const g = h('g', {
      class: 'pipe-node' + (selected === n.id ? ' on' : '') + (['RUNNING', 'READY', 'WAITING'].includes(st.state) ? ' live' : ''),
      transform: `translate(${n.x},${n.y})`
    },
      h('circle', { r: R, class: 'pipe-disk' }),
      h('text', { class: 'pipe-label', y: 4, 'text-anchor': 'middle' }, n.id),
      h('title', null, `${ag.name || n.id}\n${ag.role || ''}\n${ag.goal || ''}\n${st.state || 'idle'}`));

    let drag = false, moved = false, ox = 0, oy = 0, sx = 0, sy = 0;
    g.addEventListener('pointerdown', e => {
      drag = true; moved = false; ox = e.clientX; oy = e.clientY; sx = e.clientX; sy = e.clientY;
      g.setPointerCapture(e.pointerId); e.preventDefault();
    });
    g.addEventListener('pointermove', e => {
      if (!drag) return;
      const rect = svg.getBoundingClientRect();
      const sxScale = W / rect.width, syScale = H / rect.height;
      n.x = Math.max(R, Math.min(W - R, n.x + (e.clientX - ox) * sxScale));
      n.y = Math.max(R, Math.min(H - R, n.y + (e.clientY - oy) * syScale));
      if (Math.hypot(e.clientX - sx, e.clientY - sy) > 4) moved = true;
      ox = e.clientX; oy = e.clientY;
      g.setAttribute('transform', `translate(${n.x},${n.y})`);
      redrawEdges(gEdges, members, byId);
    });
    g.addEventListener('pointerup', () => {
      if (!drag) return;
      drag = false;
      if (moved) {
        if (onDragEnd) onDragEnd(Object.fromEntries(nodes.map(x => [x.id, { x: Math.round(x.x), y: Math.round(x.y) }])));
      } else if (selected && selected !== n.id && onToggleLink) onToggleLink(selected, n.id);
      else if (onSelect) onSelect(selected === n.id ? null : n.id);
    });
    gNodes.appendChild(g);
  }
  svg.appendChild(gEdges); svg.appendChild(gNodes);
  svg.addEventListener('click', () => onSelect && onSelect(null));
  return h('div', { class: 'pipe-wrap' },
    svg,
    h('div', { class: 'muted small' },
      selected
        ? `Selected ${selected}: click another agent to cycle link kind (none → agent → both → repository). Drag to move.`
        : 'Click an agent, then another to link (agent / both / repository). Drag to place.'));
}

export function workflowCanvas({ workflow, run, ui }) {
  const nodes = workflow.nodes || [];
  const laid = layoutWf(nodes, ui);
  const byId = Object.fromEntries(laid.map(n => [n.id, n]));
  const height = Math.max(H, 100 + Math.ceil(nodes.length / 3) * 100);
  const svg = h('svg', { class: 'pipe-canvas', viewBox: `0 0 ${W} ${height}`, width: '100%', height: String(height) });
  const gEdges = h('g', { class: 'pipe-edges' });
  const gNodes = h('g', { class: 'pipe-nodes' });
  for (const e of workflow.edges || []) {
    const a = byId[e.from], b = byId[e.to];
    if (a && b) gEdges.appendChild(arrow(a.x, a.y, b.x, b.y, e.when ? 'pipe-edge dim' : 'pipe-edge', 36));
  }
  for (const n of laid) {
    const cur = run && run.cursor === n.id;
    gNodes.appendChild(h('g', { class: 'pipe-node wf' + (cur ? ' live' : ''), transform: `translate(${n.x},${n.y})` },
      h('rect', { class: 'pipe-box', x: -52, y: -24, width: 104, height: 48, rx: 8 }),
      h('text', { class: 'pipe-label', y: -2, 'text-anchor': 'middle' }, n.id),
      h('text', { class: 'pipe-sub', y: 14, 'text-anchor': 'middle' }, n.n.type + (n.n.agent ? ` · ${n.n.agent}` : ''))));
  }
  svg.appendChild(gEdges); svg.appendChild(gNodes);
  return h('div', { class: 'pipe-wrap' }, svg);
}
