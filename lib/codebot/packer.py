"""Deterministic context packing (F8). The harness, not the model, decides what the model sees: the task and its frozen
check, the last error, the files the task names, a compact repo/symbol map, the agreed decisions, a few relevant lessons.
Nothing else, within a token budget."""
import json, os, re, subprocess
from . import checks as C, memory, modes
from .config import now

CHARS_PER_TOKEN = 3.5
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".mypy_cache", ".pytest_cache", ".idea", ".vscode"}
SYMBOL_RES = {
    ".py": re.compile(r"^\s*(?:async\s+)?(?:def|class)\s+([A-Za-z_]\w*)"),
    ".js": re.compile(r"^\s*(?:export\s+)?(?:async\s+)?(?:function\s+([A-Za-z_$][\w$]*)|class\s+([A-Za-z_$][\w$]*)|const\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\()"),
    ".sh": re.compile(r"^\s*(?:function\s+)?([A-Za-z_][\w-]*)\s*\(\)\s*\{"),
    ".go": re.compile(r"^func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)"),
    ".rs": re.compile(r"^\s*(?:pub\s+)?(?:fn|struct|enum|trait)\s+([A-Za-z_]\w*)"),
    ".c": re.compile(r"^[A-Za-z_][\w\s\*]*\s+\**([A-Za-z_]\w*)\s*\([^;]*$"),
}
for _e in (".ts", ".tsx", ".jsx", ".mjs"): SYMBOL_RES[_e] = SYMBOL_RES[".js"]
for _e in (".h", ".cpp", ".cc", ".hpp"): SYMBOL_RES[_e] = SYMBOL_RES[".c"]

def tok(s): return int(len(s) / CHARS_PER_TOKEN) + 1

def list_files(rundir, limit=400):
    r = subprocess.run(["git", "-C", rundir, "ls-files", "-z", "--cached", "--others", "--exclude-standard"], capture_output=True, text=True)
    files = [f for f in r.stdout.split("\0") if f] if r.returncode == 0 else []
    if not files:
        for dp, dn, fn in os.walk(rundir):
            dn[:] = [d for d in dn if d not in SKIP_DIRS]
            files += [os.path.relpath(os.path.join(dp, f), rundir) for f in fn]
    files = [f for f in files if not any(p in SKIP_DIRS for p in f.split("/"))]
    return sorted(files)[:limit]

def symbols(rundir, rel, max_syms=12):
    rx = SYMBOL_RES.get(os.path.splitext(rel)[1].lower())
    if not rx: return []
    out = []
    try:
        with open(os.path.join(rundir, rel), errors="replace") as f:
            for n, ln in enumerate(f, 1):
                m = rx.match(ln)
                if m:
                    out.append(f"{next(g for g in m.groups() if g)}:{n}")
                    if len(out) >= max_syms: break
                if n > 4000: break
    except OSError: pass
    return out

def repo_map(rundir, budget_chars):
    files = list_files(rundir); lines = []
    for f in files:
        syms = symbols(rundir, f)
        lines.append(f"{f}" + (f"  [{', '.join(syms)}]" if syms else ""))
    text = "\n".join(lines)
    if len(text) <= budget_chars: return text, len(files)
    # over budget: files first, symbols only for the earliest files
    out, used = [], 0
    for ln in lines:
        short = ln.split("  [")[0] if used > budget_chars * 0.5 else ln
        if used + len(short) + 1 > budget_chars: out.append(f"... ({len(files) - len(out)} more files)"); break
        out.append(short); used += len(short) + 1
    return "\n".join(out), len(files)

def _read(rundir, rel, max_lines=150, max_chars=6000):
    try:
        with open(os.path.join(rundir, rel), errors="replace") as f: lines = f.read().splitlines()
    except OSError: return None
    text = "\n".join(f"{i}: {l}" for i, l in enumerate(lines[:max_lines], 1))
    if len(lines) > max_lines: text += f"\n... ({len(lines) - max_lines} more lines; use read_file with offset)"
    return text[:max_chars]

def relevant_files(rundir, task, last_error, files, cap=4):
    """Files the task names, then paths mentioned in its check/error, then name matches against the title."""
    names = set(files); ranked = []
    def add(rel):
        if rel in names and rel not in ranked: ranked.append(rel)
    for f in task.get("files") or []: add(f.strip())
    for src in (task.get("check") or "", last_error or ""):
        for m in re.findall(r"[\w./-]+\.\w{1,5}", src): add(m.lstrip("./"))
    words = {w for w in re.findall(r"[a-z]{4,}", (task["title"] or "").lower())}
    scored = sorted(((sum(w in f.lower() for w in words), f) for f in files if f not in ranked), reverse=True)
    for sc, f in scored:
        if sc > 0: add(f)
    return ranked[:cap]

def section_from_md(path, headings, cap):
    try:
        with open(path) as f: text = f.read()
    except OSError: return ""
    out = []
    for h in headings:
        m = re.search(rf"^#\s*{h}\s*$(.*?)(?=^#\s|\Z)", text, re.M | re.S | re.I)
        if m and m.group(1).strip(): out.append(f"{h}:\n{m.group(1).strip()}")
    return "\n".join(out)[:cap]

def lessons_for(proj, task, cap=700):
    """Relevant, non-pruned lessons from the evidence-backed store: [(id, text)]."""
    return [(l["id"], l["text"]) for l in memory.select(proj, task, cap=cap)]

def previous_attempts(task, cap=5):
    h = [x for x in task.get("history", []) if "attempt" in x][-cap:]
    return "\n".join(f"- attempt {x['attempt']} (rung {x.get('rung', 1)}): check exit {x['check_rc']}"
                     + (f", TAMPERED {x['tamper']}" if x.get("tamper") else "") for x in h)

def fixture_text(proj, task, cap=1800):
    """The frozen check script plus any fixture file it references: the worker must see what it has to satisfy."""
    out = []
    script = os.path.join(C.checks_dir(proj), C.script_rel(task["id"]))
    try:
        with open(script) as f: out.append("check script:\n" + "".join(f.read().splitlines(True)[3:]))
    except OSError: pass
    for m in set(re.findall(r"\$CODEBOT_CHECKS/([\w.\-]+)", task.get("check") or "")):
        try:
            with open(os.path.join(C.checks_dir(proj), m)) as f: out.append(f"fixture {m}:\n{f.read()[:cap]}")
        except OSError: pass
    return "\n".join(out)[:cap + 600]

def pack(proj, task, last_error="", budget_tokens=None):
    """Build the initial user message for one attempt. Returns (text, manifest)."""
    budget = (budget_tokens or proj.get("CONTEXT_BUDGET", 8000, int)) * CHARS_PER_TOKEN
    rundir = proj.rundir; files = list_files(rundir)
    secs = []   # (name, text, priority) ; lower priority number = kept first
    secs.append(("TASK", f"id: {task['id']}\ntitle: {task['title']}\nsize: {task.get('size', 'M')}\nfiles hint: {', '.join(task.get('files') or []) or '(none)'}", 0))
    secs.append(("ACCEPTANCE CHECK (frozen; you cannot change it; the task is done only when it exits 0)", fixture_text(proj, task) or f"command: {task.get('check')}", 0))
    if last_error: secs.append(("LAST CHECK OUTPUT", last_error[-1500:], 1))
    ans = ""
    try:
        with open(proj.p("ANSWERS.md")) as f: ans = f.read()[-900:].strip()
    except OSError: pass
    if ans: secs.append(("HUMAN ANSWERS (override your assumptions)", ans, 1))
    dec = section_from_md(proj.p("GRILL.md"), ["Decisions", "Assumptions", "Non-goals"], 1400)
    plan = section_from_md(proj.p("PLAN.md"), ["Goal", "Definition of done"], 900)
    if dec: secs.append(("AGREED DECISIONS", dec, 2))
    if plan: secs.append(("PROJECT GOAL", plan, 3))
    try: mnotes = modes.notes(proj.home, proj.mode)
    except modes.ModeError: mnotes = ""
    if mnotes: secs.append((f"MODE NOTES ({proj.mode})", mnotes, 3))
    pa = previous_attempts(task)
    if pa: secs.append(("PREVIOUS ATTEMPTS AT THIS TASK", pa, 2))
    les = lessons_for(proj, task)
    if les: secs.append(("LESSONS", "\n".join(f"- {t}" for _, t in les), 4))
    rel = relevant_files(rundir, task, last_error, files)
    fixed = sum(len(t) + len(n) + 8 for n, t, _ in secs)
    remaining = max(1500, budget - fixed)
    map_budget = int(remaining * 0.30); file_budget = int(remaining - map_budget)
    rm, nfiles = repo_map(rundir, map_budget)
    secs.append((f"REPO MAP ({nfiles} files; name [symbol:line])", rm or "(empty)", 5))
    shown, used = [], 0
    for r in rel:
        body = _read(rundir, r, max_chars=int(max(500, min(6000, file_budget - used))))
        if body is None or used + len(body) > file_budget: continue
        shown.append(f"--- {r} ---\n{body}"); used += len(body)
    if shown: secs.append(("RELEVANT FILES", "\n".join(shown), 3))
    text = "\n\n".join(f"## {n}\n{t}" for n, t, _ in secs if t)
    while len(text) > budget and secs:      # still too big: drop the least important section first
        drop = max(range(len(secs)), key=lambda i: (secs[i][2], len(secs[i][1])))
        if secs[drop][2] == 0: text = text[:int(budget)]; break
        secs.pop(drop); text = "\n\n".join(f"## {n}\n{t}" for n, t, _ in secs if t)
    manifest = {"ts": now(), "task": task["id"], "tokens": tok(text), "budget_tokens": int(budget / CHARS_PER_TOKEN),
                "sections": [(n.split(" (")[0], tok(t)) for n, t, _ in secs if t], "files_shown": rel,
                "lessons": [i for i, _ in les] if any(n == "LESSONS" for n, _, _ in secs) else []}
    return text, manifest
