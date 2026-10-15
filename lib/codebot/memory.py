"""Evidence-backed memory (F9). `lessons.raw.jsonl` is append-only and never rewritten. Every attempt records which lessons
were packed into its context and whether it passed; lessons that correlate with failure are pruned (a flag in
lessons.status.json, never a deletion). `lessons.md` is GENERATED from the raw log under a size cap."""
import fcntl, hashlib, json, os, re
from .config import now, append_jsonl

def _paths(home):
    """`home` is the codebot home (its shared memory/ folder) or a Project, whose MEMORY_DIR may point elsewhere (benchmark trials get their own)."""
    d = getattr(home, "memdir", None) or os.path.join(home, "memory")
    return d, os.path.join(d, "lessons.raw.jsonl"), os.path.join(d, "lessons.status.json"), os.path.join(d, "lessons.md")

def _lid(text): return hashlib.sha1(re.sub(r"\s+", " ", text.strip().lower()).encode()).hexdigest()[:8]

def load_raw(home):
    _, raw, _, _ = _paths(home); out = {}
    try:
        with open(raw) as f:
            for ln in f:
                try: r = json.loads(ln)
                except ValueError: continue
                out.setdefault(r["id"], r)
    except OSError: pass
    return out

class _Status:
    """Locked read-modify-write of the status file (several projects may run at once)."""
    def __init__(self, home): self.path = _paths(home)[2]; os.makedirs(os.path.dirname(self.path), exist_ok=True)
    def __enter__(self):
        self.lf = open(self.path + ".lock", "a"); fcntl.flock(self.lf, fcntl.LOCK_EX)
        try:
            with open(self.path) as f: self.s = json.load(f)
        except (OSError, ValueError): self.s = {}
        self.s.setdefault("lessons", {}); self.s.setdefault("baseline", {"n": 0, "pass": 0}); return self.s
    def __exit__(self, *a):
        if a[0] is None:
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f: json.dump(self.s, f, indent=1)
            os.replace(tmp, self.path)
        fcntl.flock(self.lf, fcntl.LOCK_UN); self.lf.close()

def _entry(s, lid): return s["lessons"].setdefault(lid, {"used": 0, "pass": 0, "pruned": False, "sources": []})

def add(home, text, project, task, attempt=0):
    """Append a lesson. Returns its id, or None if it is too short or already known (the duplicate's source is still recorded)."""
    text = (text or "").strip()[:300]
    if len(text) < 12: return None
    lid = _lid(text)
    with _Status(home) as s:
        e = _entry(s, lid); src = {"project": project, "task": task}
        if src not in e["sources"]: e["sources"].append(src)
        known = lid in load_raw(home)
    if known: return None
    append_jsonl(_paths(home)[1], {"id": lid, "ts": now(), "text": text, "project": project, "task": task, "attempt": attempt})
    rebuild_md(home)
    return lid

def rate(e): return (e["pass"] + 1) / (e["used"] + 2)       # Laplace-smoothed pass rate of attempts that used the lesson

def select(proj, task, k=4, cap=700):
    """Relevant, non-pruned lessons for a task: [{"id","text"}]. Ranked by word overlap, then by evidence."""
    raw = load_raw(proj)
    if not raw: return []
    with _Status(proj) as s: st = {i: dict(e) for i, e in s["lessons"].items()}
    words = set(re.findall(r"[a-z]{4,}", (task["title"] + " " + " ".join(task.get("files") or [])).lower()))
    scored = []
    for lid, r in raw.items():
        e = st.get(lid) or {"used": 0, "pass": 0, "pruned": False}
        if e.get("pruned"): continue
        ov = sum(w in r["text"].lower() for w in words)
        if ov: scored.append((ov, rate(e), r["ts"], lid, r["text"]))
    scored.sort(reverse=True)
    out, used = [], 0
    for _, _, _, lid, text in scored[:k]:
        if used + len(text) > cap: break
        out.append({"id": lid, "text": text}); used += len(text)
    return out

def record_outcome(home, lesson_ids, passed):
    """One attempt finished. The baseline counts every attempt; each packed lesson counts its own."""
    with _Status(home) as s:
        s["baseline"]["n"] += 1; s["baseline"]["pass"] += 1 if passed else 0
        for lid in lesson_ids:
            e = _entry(s, lid); e["used"] += 1; e["pass"] += 1 if passed else 0

def prune(home, min_samples=8, margin=0.15, min_baseline=20):
    """Flag lessons whose attempts pass clearly less often than the overall baseline. Conservative on purpose:
    correlation is not causation, so it needs enough samples on both sides and a real margin. Returns newly pruned ids."""
    newly = []
    with _Status(home) as s:
        b = s["baseline"]
        if b["n"] < min_baseline: return []
        base = b["pass"] / b["n"]
        for lid, e in s["lessons"].items():
            if e.get("pruned") or e["used"] < min_samples: continue
            r = e["pass"] / e["used"]
            if r < base - margin:
                e["pruned"] = True; e["pruned_reason"] = f"{e['pass']}/{e['used']} attempts passed vs baseline {base:.0%}"; e["pruned_at"] = now(); newly.append(lid)
    if newly: rebuild_md(home)
    return newly

def rebuild_md(home, cap=None):
    """Regenerate lessons.md from the raw log: active lessons, best evidence first, within the size cap."""
    d, _, _, md = _paths(home)
    if cap is None:
        try: cap = int(next((l.split("=")[1].split("#")[0].strip().strip('"') for l in open(os.path.join(getattr(home, "home", home), "config", "codebot.env")) if l.startswith("LESSONS_MAX=")), 4000))
        except (OSError, ValueError, StopIteration): cap = 4000
    raw = load_raw(home)
    with _Status(home) as s: st = {i: dict(e) for i, e in s["lessons"].items()}
    act = sorted(((rate(st.get(i, {"used": 0, "pass": 0})), r["ts"], i, r["text"]) for i, r in raw.items() if not st.get(i, {}).get("pruned")), reverse=True)
    lines = ["# Lessons (generated from memory/lessons.raw.jsonl; add new ones with the note_lesson action, do not edit this file)"]
    size = len(lines[0]) + 1
    for _, _, i, text in act:
        ln = f"- {text}"
        if size + len(ln) + 1 > cap: break
        lines.append(ln); size += len(ln) + 1
    os.makedirs(d, exist_ok=True); tmp = md + ".tmp"
    with open(tmp, "w") as f: f.write("\n".join(lines) + "\n")
    os.replace(tmp, md)

def summary(home):
    raw = load_raw(home)
    with _Status(home) as s: st = s["lessons"]; base = dict(s["baseline"])
    pruned = [i for i, e in st.items() if e.get("pruned")]
    return {"total": len(raw), "active": len(raw) - len([i for i in pruned if i in raw]), "pruned": len(pruned), "pruned_ids": pruned,
            "attempts_seen": base["n"], "baseline_pass": round(base["pass"] / base["n"], 2) if base["n"] else None}
