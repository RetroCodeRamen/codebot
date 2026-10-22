// A small offline syntax highlighter: keywords, strings, comments, numbers, calls and types. Output is [[cls, text], ...] per line; the caller
// puts each piece in a span as text, so highlighting can never turn file content into markup.
const KW = {
  go: 'break case chan const continue default defer else fallthrough for func go goto if import interface map package range return select struct switch type var nil true false iota',
  py: 'and as assert async await break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield None True False self',
  js: 'async await break case catch class const continue debugger default delete do else export extends finally for from function if import in instanceof let new of return static super switch this throw try typeof var void while with yield null undefined true false',
  sh: 'if then else elif fi for while until do done case esac function in return local export set exit source echo',
  c: 'auto break case char const continue default do double else enum extern float for goto if int long register return short signed sizeof static struct switch typedef union unsigned void volatile while include define NULL',
  rs: 'as break const continue crate else enum extern false fn for if impl in let loop match mod move mut pub ref return self Self static struct super trait true type unsafe use where while',
};
const TYPES = { go: 'bool byte error float32 float64 int int8 int16 int32 int64 rune string uint uint8 uint16 uint32 uint64 any', py: 'int float str bool list dict set tuple bytes object', c: '', js: '', sh: '', rs: 'i8 i16 i32 i64 u8 u16 u32 u64 f32 f64 bool char str String Vec Option Result' };
const sets = {}; for (const k in KW) sets[k] = { kw: new Set(KW[k].split(' ')), ty: new Set((TYPES[k] || '').split(' ').filter(Boolean)) };

export function langOf(path) {
  const p = (path || '').toLowerCase();
  if (p.endsWith('.go') || p === 'go.mod') return 'go';
  if (p.endsWith('.py')) return 'py';
  if (/\.(js|mjs|ts|tsx|jsx|json)$/.test(p)) return 'js';
  if (/\.(sh|bash|env)$/.test(p) || p === 'project.env') return 'sh';
  if (/\.(c|h|cc|cpp|hpp)$/.test(p)) return 'c';
  if (p.endsWith('.rs')) return 'rs';
  if (/\.(md|txt|log|jsonl)$/.test(p) || p.startsWith('report-')) return null;
  return null;
}

export function tokenize(text, lang) {
  const out = []; const s = sets[lang]; const n = text.length; let i = 0;
  const push = (cls, t) => { if (t) out.push([cls, t]); };
  if (!s) { push('', text); return split(out); }
  const lineCom = lang === 'py' || lang === 'sh' ? '#' : '//';
  while (i < n) {
    const c = text[i], two = text.substr(i, 2);
    if (text.startsWith(lineCom, i) && !(lang === 'sh' && i > 0 && /[\w$]/.test(text[i - 1]))) {
      let j = text.indexOf('\n', i); if (j < 0) j = n; push('tk-com', text.slice(i, j)); i = j; continue;
    }
    if (two === '/*' && lang !== 'py' && lang !== 'sh') { let j = text.indexOf('*/', i + 2); j = j < 0 ? n : j + 2; push('tk-com', text.slice(i, j)); i = j; continue; }
    if (lang === 'py' && (text.startsWith('"""', i) || text.startsWith("'''", i))) {
      const q = text.substr(i, 3); let j = text.indexOf(q, i + 3); j = j < 0 ? n : j + 3; push('tk-str', text.slice(i, j)); i = j; continue;
    }
    if (c === '"' || c === "'" || (c === '`' && (lang === 'go' || lang === 'js'))) {
      let j = i + 1;
      while (j < n && text[j] !== c) { if (text[j] === '\\' && c !== '`') j++; if (text[j] === '\n' && c !== '`') break; j++; }
      j = Math.min(n, j + 1); push('tk-str', text.slice(i, j)); i = j; continue;
    }
    if (/[0-9]/.test(c) && !(i > 0 && /[\w]/.test(text[i - 1]))) {
      const m = /^(0x[0-9a-fA-F_]+|[0-9][0-9_]*(\.[0-9_]+)?([eE][+-]?[0-9]+)?)/.exec(text.slice(i, i + 40)); push('tk-num', m[0]); i += m[0].length; continue;
    }
    if (/[A-Za-z_]/.test(c)) {
      const m = /^[A-Za-z_][A-Za-z0-9_]*/.exec(text.slice(i, i + 200)); const w = m[0]; let j = i + w.length;
      let k = j; while (k < n && text[k] === ' ') k++;
      push(s.kw.has(w) ? 'tk-kw' : s.ty.has(w) ? 'tk-type' : text[k] === '(' ? 'tk-fn' : '', w); i = j; continue;
    }
    let j = i + 1; while (j < n && !/[A-Za-z0-9_"'`#\/]/.test(text[j]) && text[j] !== '\n') j++;
    if (text[i] === '\n') { push('', '\n'); i++; continue; }
    push('', text.slice(i, j)); i = j;
  }
  return split(out);
}
function split(tokens) {
  const lines = [[]];
  for (const [cls, t] of tokens) {
    const parts = t.split('\n');
    parts.forEach((p, k) => { if (k > 0) lines.push([]); if (p) lines[lines.length - 1].push([cls, p]); });
  }
  return lines;
}
