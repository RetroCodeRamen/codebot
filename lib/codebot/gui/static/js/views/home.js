// Home redirects to the Projects library (primary surface).
export function mount() {
  location.replace('#/projects');
  return () => {};
}

/** Shared one-line progress for library cards and lists. */
export function progress(p) {
  if (p.workflow === 'shape') {
    const s = p.shape || {};
    if (s.busy) return 'Reading it now';
    if (s.phase === 'questions') return 'Questions for you';
    if (s.phase === 'ready') return 'Ready to build';
    return 'Working out the shape';
  }
  if (p.workflow === 'drafts') {
    const d = p.drafts || {};
    if (!d.started) return 'Not started';
    return `Round ${d.round} · draft ${d.draft} · ${d.best ? `best ${d.best.examples}/${d.examples_total}` : 'nothing builds yet'} · ${d.used_minutes}/${d.budget_minutes ?? '?'} min`;
  }
  const t = p.tasks || {};
  return t.frozen ? Object.entries(t.counts || {}).map(([k, v]) => `${v} ${k}`).join(', ') : (t.plan ? 'Plan not frozen' : 'No plan');
}
