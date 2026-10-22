// A safe Markdown subset (reports, specs): headings, paragraphs, lists, quotes, fenced code, simple tables, `code`, **bold**, *em*.
// It builds DOM nodes from text; raw HTML in the source stays visible as text, and links are shown, not followed.
import { h } from './dom.js';

export function render(src) {
  const root = h('div', { class: 'md' }); const lines = (src || '').replace(/\r\n?/g, '\n').split('\n');
  let i = 0;
  while (i < lines.length) {
    const ln = lines[i];
    if (/^```/.test(ln)) {
      const body = []; i++;
      while (i < lines.length && !/^```/.test(lines[i])) body.push(lines[i++]);
      i++; root.appendChild(h('pre', null, h('code', null, body.join('\n')))); continue;
    }
    const hm = /^(#{1,6})\s+(.*)$/.exec(ln);
    if (hm) { root.appendChild(h('h' + Math.min(3, hm[1].length), null, inline(hm[2]))); i++; continue; }
    if (/^\s*([-*+]|\d+[.)])\s+/.test(ln)) {
      const ordered = /^\s*\d/.test(ln); const list = h(ordered ? 'ol' : 'ul');
      while (i < lines.length && /^\s*([-*+]|\d+[.)])\s+/.test(lines[i])) {
        let item = lines[i].replace(/^\s*([-*+]|\d+[.)])\s+/, ''); i++;
        while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !/^\s*([-*+]|\d+[.)])\s+/.test(lines[i])) item += ' ' + lines[i++].trim();
        list.appendChild(h('li', null, inline(item)));
      }
      root.appendChild(list); continue;
    }
    if (/^>\s?/.test(ln)) {
      const body = []; while (i < lines.length && /^>\s?/.test(lines[i])) body.push(lines[i++].replace(/^>\s?/, ''));
      root.appendChild(h('blockquote', { class: 'note' }, inline(body.join(' ')))); continue;
    }
    if (/^\|.*\|\s*$/.test(ln)) {
      const rows = []; while (i < lines.length && /^\|.*\|\s*$/.test(lines[i])) rows.push(lines[i++]);
      const cells = r => r.trim().replace(/^\||\|$/g, '').split('|').map(c => c.trim());
      const t = h('table'); rows.forEach((r, k) => {
        if (k === 1 && /^[\s|:-]+$/.test(r)) return;
        t.appendChild(h('tr', null, cells(r).map(c => h(k === 0 ? 'th' : 'td', null, inline(c)))));
      });
      root.appendChild(t); continue;
    }
    if (!ln.trim()) { i++; continue; }
    const para = []; while (i < lines.length && lines[i].trim() && !/^(```|#{1,6}\s|>|\|)/.test(lines[i]) && !/^\s*([-*+]|\d+[.)])\s+/.test(lines[i])) para.push(lines[i++]);
    root.appendChild(h('p', null, inline(para.join(' '))));
  }
  return root;
}

function inline(text) {
  const out = []; let rest = text;
  const re = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*\s][^*]*\*)|(\[[^\]]+\]\([^)\s]+\))/;
  while (rest) {
    const m = re.exec(rest);
    if (!m) { out.push(rest); break; }
    if (m.index) out.push(rest.slice(0, m.index));
    const t = m[0];
    if (m[1]) out.push(h('code', null, t.slice(1, -1)));
    else if (m[2]) out.push(h('strong', null, t.slice(2, -2)));
    else if (m[3]) out.push(h('em', null, t.slice(1, -1)));
    else { const lm = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(t); out.push(lm[1], h('span', { class: 'muted small' }, ` (${lm[2]})`)); }
    rest = rest.slice(m.index + t.length);
  }
  return out;
}
