"""Morning report (F12): one summary of an unattended run, built only from the harness's own records
(tasks.json, attempts.jsonl, plan.log, checkpoints, git, the lessons store and the audit log)."""
import json, os, re, subprocess, time
from . import audit, gitops as G, memory, tasks as T, sandbox
from .config import now

RISKY_FILE = re.compile(r"(^|/)(package(-lock)?\.json|requirements[\w.-]*\.txt|pyproject\.toml|setup\.py|Cargo\.(toml|lock)|go\.(mod|sum)|Dockerfile|Makefile|\.env[\w.]*|\.github/.*|.*\.(sh|service|timer))$")

def _jsonl(path):
    out = []
    try:
        with open(path) as f:
            for ln in f:
                try: out.append(json.loads(ln))
                except ValueError: pass
    except OSError: pass
    return out

def _started(proj):
    try:
        with open(proj.p("started.at")) as f: return int(f.read().strip())
    except (OSError, ValueError): return None

def _fmt_dur(sec):
    sec = int(max(0, sec)); h, m = divmod(sec // 60, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"

def commit_stats(proj, sha):
    r = G.git(proj.rundir, "show", "--numstat", "--format=", sha)
    files, adds, dels = [], 0, 0
    for ln in r.stdout.splitlines():
        parts = ln.split("\t")
        if len(parts) == 3:
            a, d, f = parts; a = int(a) if a.isdigit() else 0; d = int(d) if d.isdigit() else 0
            files.append({"path": f, "add": a, "del": d}); adds += a; dels += d
    return files, adds, dels

def review_list(proj, data, top=5):
    """Commits worth a human look, with the reason. Score = size + heavy weight for risk flags."""
    cps = G.load(proj)["list"]; by_task = {t["id"]: t for t in data["tasks"]}; out = []
    for cp in cps:
        files, adds, dels = commit_stats(proj, cp["sha"]); task = by_task.get(cp["task"], {}); hint = set(task.get("files") or [])
        why = []
        outside = [f["path"] for f in files if hint and f["path"] not in hint and not f["path"].startswith(tuple(h.rstrip("/") + "/" for h in hint if h.endswith("/")))]
        if outside: why.append(f"touched files outside the task's hint: {', '.join(outside[:3])}")
        risky = [f["path"] for f in files if RISKY_FILE.search(f["path"])]
        if risky: why.append(f"changes build/config/script files: {', '.join(risky[:3])}")
        if dels >= 40 and dels > adds: why.append(f"mostly deletions ({dels} lines removed)")
        if adds + dels >= 300: why.append(f"large change ({adds + dels} lines)")
        if any(f["add"] == 0 and f["del"] == 0 for f in files): why.append("binary or mode-only change")
        out.append({"task": cp["task"], "title": task.get("title", ""), "sha": cp["sha"][:8], "files": len(files), "add": adds, "del": dels,
                    "why": why, "score": adds + dels + 250 * len(why)})
    out.sort(key=lambda x: -x["score"]); return out[:top]

def build(proj):
    store = T.TaskStore(proj)
    if not store.exists(): return None
    data = store.load(); tasks = data["tasks"]
    atts = [a for a in _jsonl(proj.p("attempts.jsonl")) if "attempt" in a]; ev = _jsonl(proj.p("attempts.jsonl"))
    plan_log = _jsonl(proj.p("plan.log")); started = _started(proj); elapsed = time.time() - started if started else None
    done = [t for t in tasks if t["status"] == "done"]; blocked = [t for t in tasks if t["status"] == "blocked"]
    pending = [t for t in tasks if t["status"] in ("pending", "running")]
    mem_raw = memory.load_raw(proj.home); since = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(started)) if started else ""
    new_lessons = [r for r in mem_raw.values() if r["ts"] >= since and r.get("project") == proj.name]
    with memory._Status(proj.home) as st: pruned = [dict(id=i, **e) for i, e in st["lessons"].items() if e.get("pruned") and e.get("pruned_at", "") >= since]
    aud = audit.report(proj); mine = [e for e in audit.entries(proj) if e.get("kind") == "model_request"]
    worker_secs = sum(a.get("worker_secs") or 0 for a in atts); check_secs = sum(a.get("secs") or 0 for a in atts)
    esc = [l for l in plan_log if l.get("op") == "escalate"]; model_ops = [l for l in plan_log if l.get("actor") == "model"]
    hard = [t for t in done if t["attempts"] >= 3]
    r = {"project": proj.name, "mode": proj.mode, "generated": now(), "elapsed": elapsed,
         "counts": {"total": len(tasks), "done": len(done), "blocked": len(blocked), "pending": len(pending)},
         "blocked": [{"id": t["id"], "title": t["title"], "question": t.get("blocked_question") or ""} for t in blocked],
         "pending": [{"id": t["id"], "title": t["title"], "waits_on": [d for d in t["deps"] if d not in {x["id"] for x in done}]} for t in pending],
         "done": [{"id": t["id"], "title": t["title"], "attempts": t["attempts"], "commit": (t.get("commit") or "")[:8]} for t in done],
         "hard_won": [t["id"] for t in hard], "split": [{"id": t["id"], "children": t["split_into"]} for t in tasks if t.get("split_into")],
         "review": review_list(proj, data), "weak_checks": [l["reason"] for l in plan_log if l.get("op") == "weak_check"],
         "replans": {"accepted": sum(1 for l in model_ops if l.get("ok")), "rejected": sum(1 for l in model_ops if not l.get("ok")), "rejected_why": [l.get("error") for l in model_ops if not l.get("ok")][:3]},
         "escalations": len(esc), "tamper": sum(1 for e in ev if e.get("event") == "tamper"), "interrupted": sum(1 for e in ev if e.get("event") == "interrupted"),
         "attempts": len(atts), "attempts_by_rung": {str(k): sum(1 for a in atts if a.get("rung", 1) == k) for k in sorted({a.get("rung", 1) for a in atts})},
         "worker_secs": worker_secs, "check_secs": check_secs, "tokens": sum(e.get("prompt_tokens", 0) + e.get("gen_tokens", 0) for e in mine), "model_requests": len(mine),
         "lessons_added": [{"id": r_["id"], "text": r_["text"], "task": r_.get("task")} for r_ in new_lessons], "lessons_pruned": [{"id": p["id"], "why": p.get("pruned_reason", "")} for p in pruned],
         "audit": {"chain_ok": aud["chain_ok"], "unsandboxed": aud["unsandboxed"], "warnings": aud["warnings"], "sandbox_modes": aud["sandbox_modes"], "files_written": aud["files_written"]}}
    return r

def short(r):
    c = r["counts"]; head = f"{c['done']}/{c['total']} tasks verified by their own locked checks"
    if c["blocked"]: head += f", {c['blocked']} blocked and waiting on you"
    if c["pending"] and not c["blocked"]: head += f", {c['pending']} still pending"
    L = [head + f". {_fmt_dur(r['elapsed']) if r['elapsed'] else '?'} total, {r['attempts']} attempts."]
    for b in r["blocked"][:2]: L.append(f"BLOCKED {b['id']}: {b['question'][:200]}")
    if r["review"] and r["review"][0]["why"]: L.append(f"Review first: {r['review'][0]['task']} ({r['review'][0]['why'][0][:90]})")
    if r["audit"]["warnings"]: L.append("Safety: " + r["audit"]["warnings"][0][:120])
    elif r["audit"]["chain_ok"]: L.append("Safety: audit chain intact, commands ran offline in the sandbox." if not r["audit"]["unsandboxed"] else "Safety: some commands were NOT sandboxed.")
    if r["lessons_added"] or r["lessons_pruned"]: L.append(f"Memory: +{len(r['lessons_added'])} lessons, {len(r['lessons_pruned'])} pruned.")
    return "\n".join(L)[:1200]

def markdown(r):
    c = r["counts"]; L = [f"# codebot morning report: {r['project']}", f"_{r['generated']} · mode {r['mode']}_", "",
         f"**{c['done']} of {c['total']} tasks verified** by their own frozen checks · {c['blocked']} blocked · {c['pending']} pending · "
         f"{_fmt_dur(r['elapsed']) if r['elapsed'] else 'unknown'} elapsed · {r['attempts']} attempts · {r['tokens']:,} model tokens", ""]
    if r["blocked"]:
        L += ["## Needs you", ""] + [f"- **{b['id']}** {b['title']}\n  - {b['question']}" for b in r["blocked"]] + ["", f"Answer with `codebot answer {r['project']} \"T#: guidance\"`; the run continues from where it stopped.", ""]
    if r["pending"] and r["blocked"]:
        L += ["Waiting on those answers: " + ", ".join(f"{p['id']} (needs {', '.join(p['waits_on']) or 'nothing'})" for p in r["pending"]), ""]
    L += ["## Done", ""] + ([f"- {d['id']} {d['title']} - {d['attempts']} attempt(s) · `{d['commit']}`" for d in r["done"]] or ["- nothing yet"]) + [""]
    if r["hard_won"]: L += [f"Needed 3+ attempts (look at these first): {', '.join(r['hard_won'])}", ""]
    L += ["## Diffs worth reviewing", ""]
    L += ([f"- **{x['task']}** `{x['sha']}` +{x['add']}/-{x['del']} in {x['files']} file(s)" + (": " + "; ".join(x["why"]) if x["why"] else "") for x in r["review"]] or ["- no commits yet"])
    L += ["", f"Review and merge with `codebot diff {r['project']}`.", ""]
    L += ["## Plan changes and escalations", "", f"- ladder escalations: {r['escalations']}; attempts by rung: {r['attempts_by_rung'] or 'none'}",
          f"- the model changed the plan {r['replans']['accepted']} time(s); {r['replans']['rejected']} request(s) were refused" + (f" ({'; '.join(map(str, r['replans']['rejected_why']))})" if r["replans"]["rejected"] else "")]
    if r["split"]: L.append("- split: " + ", ".join(f"{s['id']} -> {', '.join(s['children'])}" for s in r["split"]))
    if r["weak_checks"]: L.append("- weak checks (passed before any work): " + "; ".join(r["weak_checks"][:5]))
    if r["interrupted"]: L.append(f"- {r['interrupted']} attempt(s) were interrupted by pause/crash and not charged")
    L += ["", "## Memory", ""]
    L += ([f"- added `{l['id']}` ({l['task']}): {l['text']}" for l in r["lessons_added"][:10]] or ["- no new lessons"])
    L += [f"- pruned `{p['id']}`: {p['why']}" for p in r["lessons_pruned"]]
    L += ["", "## Time and cost", "", f"- model/worker time {_fmt_dur(r['worker_secs'])}, checks {_fmt_dur(r['check_secs'])}, {r['model_requests']} model requests"]
    a = r["audit"]
    L += ["", "## Safety", "", f"- audit chain: {'intact' if a['chain_ok'] else 'BROKEN'} · sandbox: {', '.join(a['sandbox_modes']) or 'no commands run'} · {a['unsandboxed']} unsandboxed · {a['files_written']} files written by the worker",
          f"- tamper attempts caught on locked files: {r['tamper']}"] + [f"- WARNING: {w}" for w in a["warnings"]] + [f"- details: `codebot audit {r['project']} --probe`", ""]
    return "\n".join(L)

def write(proj, r):
    path = proj.p(f"report-{time.strftime('%Y-%m-%d-%H%M')}.md")
    with open(path, "w") as f: f.write(markdown(r))
    return path
