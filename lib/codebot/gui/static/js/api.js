// The GUI's only way to the program: the local API. State-changing requests carry X-Codebot (the server refuses them without it).
const listeners = new Set();
let online = true, lastOk = Date.now();
export const conn = { get online() { return online; }, get lastOk() { return lastOk; }, on(fn) { listeners.add(fn); return () => listeners.delete(fn); } };
function setOnline(v) { if (v) lastOk = Date.now(); if (v !== online) { online = v; listeners.forEach(f => f(v)); } }

export class ApiError extends Error { constructor(status, msg) { super(msg); this.status = status; } }

async function req(path, opts) {
  let r;
  try { r = await fetch(path, { cache: 'no-store', ...opts }); }
  catch (e) { setOnline(false); throw new ApiError(0, 'no connection to the codebot GUI server'); }
  setOnline(true);
  let data = null; try { data = await r.json(); } catch (e) { /* not JSON */ }
  if (!r.ok) throw new ApiError(r.status, (data && data.error) || `error ${r.status}`);
  return data;
}
export const get = path => req(path);
export const post = (path, body) => req(path, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Codebot': '1' }, body: JSON.stringify(body || {}) });
export const q = obj => Object.entries(obj).filter(([, v]) => v !== undefined && v !== null && v !== '').map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join('&');
