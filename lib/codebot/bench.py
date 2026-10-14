"""codebot bench: run a suite of tasks through the REAL pipeline (planner optional, freeze, supervisor loop, worker, checks, ladder) with the
real model, and record what it did: pass rate, attempts, time, per-turn prompt vs generation time, schema failures, guard rejections,
every ladder step, every blocked task, heat."""
import json, os, re, shutil, statistics, subprocess, sys, time
from . import modes, planparse, tasks as T, thermal, tokens
from .config import Project, now, read_env

def suite_dir(home, suite): return os.path.join(home, "bench", "suite", suite)

def _jsonl(path):
    out = []
    try:
        with open(path) as f:
            for ln in f:
                try: out.append(json.loads(ln))
                except ValueError: pass
    except OSError: pass
    return out

def _git(d, *a): return subprocess.run(["git", "-C", d, "-c", "user.name=bench", "-c", "user.email=bench@localhost", *a], capture_output=True, text=True)

def setup(home, suite, plan="fixed", name=None):
    src = suite_dir(home, suite)
    if not os.path.isdir(src): raise SystemExit(f"unknown suite '{suite}' (bench/suite/)")
    name = name or f"bench-{suite}-{plan}-{time.strftime('%m%d-%H%M%S')}"
    state = os.path.join(home, "projects", name); code = os.path.join(home, "bench", "runs", name, "code")
    if os.path.exists(state): raise SystemExit(f"project {name} already exists")
    shutil.copytree(os.path.join(src, "seed"), code); _git(code, "init", "-q", "-b", "main"); _git(code, "add", "-A"); _git(code, "commit", "-qm", "seed")
    os.makedirs(state)
    with open(os.path.join(state, "project.env"), "w") as f:
        f.write(f'WORKDIR="{code}"\nMODE="code"\nBENCH="1"\nNTFY_ENABLED="0"\nPHONE_CONTROL="0"\n')       # a bench never pushes to your phone
    for fn in ("PROGRESS.md", "ANSWERS.md"): open(os.path.join(state, fn), "w").close()
    if plan == "fixed":
        shutil.copy(os.path.join(src, "plan.md"), os.path.join(state, "PLAN.md")); shutil.copytree(os.path.join(src, "checks"), os.path.join(state, "checks"))
    else: shutil.copy(os.path.join(src, "spec.md"), os.path.join(state, "SPEC.md"))
    return name

def _seed_sha(code):
    r = _git(code, "rev-list", "--max-parents=0", "HEAD"); return r.stdout.split()[0] if r.stdout.split() else None

def _cleanup(proj):
    """The planner must not touch code. Whatever it did is recorded as a violation and undone, so every later stage starts from the real seed."""
    code, viol = proj.rundir, []
    for ln in _git(code, "status", "--porcelain", "--untracked-files=all").stdout.splitlines():
        path = ln[3:].strip()
        if path and "__pycache__" not in path: viol.append(path)
    seed = _seed_sha(code)
    if seed: _git(code, "reset", "-q", "--hard", seed)
    _git(code, "clean", "-fdq")
    stray = os.path.join(proj.state, "code")
    if os.path.isdir(stray): viol.append("projects/<p>/code/ (a stray code dir in the state dir)"); shutil.rmtree(stray, ignore_errors=True)
    for f in os.listdir(proj.state):                      # planner-made junk next to the state files (e.g. a stray test dir)
        if f.endswith(".py") and f != "worker.py": viol.append(f"stray {f} in the state dir"); os.remove(os.path.join(proj.state, f))
    return viol

def _render(tpl, proj, name, **extra):
    spec = ""
    try:
        with open(proj.p("SPEC.md")) as f: spec = f.read()
    except OSError: pass
    out = tpl.replace("{{STATE}}", proj.state).replace("{{WORKDIR}}", proj.rundir).replace("{{NAME}}", name).replace("{{SPEC}}", spec)
    for k, v in extra.items(): out = out.replace("{{" + k + "}}", v)
    return out

def _run_planner(home, proj, prompt, timeout, planner_cmd, rnd):
    pf = proj.p(f"plan-round-{rnd}.prompt.md")
    with open(pf, "w") as f: f.write(prompt)
    env = dict(os.environ, OPENCODE_CONFIG=os.path.join(home, "config", "opencode.json"), OLLAMA_HOST=f"127.0.0.1:{proj.get('OLLAMA_PORT', 11500)}",
               PLAN_ROUND=str(rnd), PLAN_PROMPT_FILE=pf, STATE=proj.state, CODE=proj.rundir)
    with open(proj.p("plan-auto.log"), "a") as lf:
        lf.write(f"\n===== planner round {rnd} =====\n"); lf.flush()
        cmd = planner_cmd or [os.path.join(home, "tools", "node_modules", ".bin", "opencode"), "run", "--auto", "--thinking", "--variant", "nothink", "--agent", "codebot", "--dir", proj.rundir, prompt]
        try: r = subprocess.run(cmd, shell=isinstance(cmd, str), cwd=proj.rundir, env=env, stdout=lf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, timeout=timeout); return r.returncode
        except subprocess.TimeoutExpired: return 124

def plan_auto(home, name, timeout=2700, rounds=3, log=print, planner_cmd=None):
    """Unattended planner driven BY THE HARNESS: the spec is given up front; after every round the harness runs `check-plan` itself (a 9B model did not run
    it, and read 'rewrite until it says OK' as 'make the checks pass'), undoes any code the planner wrote, and sends back the exact rejection report."""
    proj = Project(name, home)
    with open(os.path.join(home, "modes", "code", "plan-auto.md")) as f: first = f.read()
    with open(os.path.join(home, "modes", "code", "plan-repair.md")) as f: repair = f.read()
    sizing = subprocess.run([sys.executable, os.path.join(home, "bin", "codebot-engine"), "sizing", name, "--home", home], capture_output=True, text=True).stdout.strip()
    info = {"rounds": [], "ok": False, "secs": 0}; t0 = time.time(); report = ""
    for k in range(1, rounds + 1):
        left = timeout - (time.time() - t0)
        if left < 60: break
        prompt = _render(first, proj, name, SIZING=sizing) if k == 1 else _render(repair, proj, name, REPORT=report)
        r0 = time.time(); rc = _run_planner(home, proj, prompt, left, planner_cmd, k); viol = _cleanup(proj)
        cp = codebot(home, "check-plan", name); out = (cp.stdout + cp.stderr).strip()
        if not os.path.exists(proj.p("PLAN.md")): out = "PLAN.md does not exist: write it in " + proj.state; ok = False
        else: ok = cp.returncode == 0
        bad = re.findall(r"^(T[\d.]+)\s+(BROKEN|PASSES|MALFORMED|NO CHECK)", out, re.M)
        info["rounds"].append({"n": k, "secs": round(time.time() - r0), "rc": rc, "rejected": [f"{t}:{v.lower()}" for t, v in bad], "violations": viol, "ok": ok})
        log(f"planner round {k}: {len(bad)} rejected, {len(viol)} violations, ok={ok}")
        if ok: info["ok"] = True; break
        report = out
    info["secs"] = round(time.time() - t0); return info

def codebot(home, *a, timeout=300): return subprocess.run([os.path.join(home, "bin", "codebot"), *a], capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)

def classify(counts, settled, loop_alive, elapsed_s):
    """Run status from what we can observe. A loop that exited AFTER every task settled finished normally (COMPLETE ends the loop):
    only a loop that is gone while work is still unsettled counts as died."""
    if counts["pending"] == 0 and counts["running"] == 0 and (settled >= 2 or not loop_alive):
        return "finished" if counts["blocked"] == 0 else "finished_with_blocked"
    if not loop_alive and elapsed_s > 60: return "loop_died"
    return None

def run_project(home, name, max_minutes=150, poll=20, log=print):
    proj = Project(name, home); t0 = time.time(); temps = []; store = T.TaskStore(proj)
    r = codebot(home, "start", name)
    if r.returncode != 0 and not os.path.exists(proj.p("tasks.json")): return {"status": "freeze_failed", "detail": (r.stdout + r.stderr).strip()[-1500:], "minutes": 0, "temps": temps}
    settled = 0; status = "timeout"
    while time.time() - t0 < max_minutes * 60:
        time.sleep(poll); t = thermal.read_temp()
        if t is not None: temps.append(round(t, 1))
        try: c = T.counts(store.load())
        except (OSError, ValueError): continue
        log(f"[{int((time.time() - t0) / 60):>3}m] {c}  temp={t}")
        pid = None
        try:
            with open(proj.p("loop.pid")) as f: pid = int(f.read().strip())
            os.kill(pid, 0)
        except (OSError, ValueError): pid = None
        if c["pending"] == 0 and c["running"] == 0: settled += 1
        else: settled = 0
        st = classify(c, settled, pid is not None, time.time() - t0)
        if st: status = st; break
    codebot(home, "stop", name)
    return {"status": status, "minutes": round((time.time() - t0) / 60, 1), "temps": temps}

def _pct(xs, p):
    xs = sorted(xs); return xs[min(len(xs) - 1, int(len(xs) * p))] if xs else None
def _stat(xs): return {"mean": round(statistics.mean(xs)), "p50": round(_pct(xs, .5)), "p95": round(_pct(xs, .95)), "n": len(xs)} if xs else None

def summarize_turns(trace):
    turns = [e for e in trace if e.get("event") == "turn"]; ends = [e for e in trace if e.get("event") == "attempt_end"]
    if not turns: return {"turns": 0}
    gen = [e for e in turns if e.get("gtok") and e.get("gen_ms")]
    first = [e for e in turns if e.get("n") == 1 and e.get("ptok") and e.get("est_prompt")]; later = [e for e in turns if (e.get("n") or 0) > 1 and e.get("ptok") and e.get("est_prompt")]
    cold = [e["ptok"] / (e["prompt_ms"] / 1000) for e in first if e.get("prompt_ms")]             # tokens/s of a cold prompt = the baseline for "what re-evaluating everything costs"
    cold_tps = statistics.median(cold) if cold else None
    reuse = lambda es: round(statistics.mean(max(0.0, 1 - e["prompt_ms"] / (e["ptok"] / cold_tps * 1000)) for e in es if e.get("prompt_ms") is not None), 2) if es and cold_tps else None
    return {"turns": len(turns), "ok": sum(1 for e in turns if e.get("kind") == "ok"), "malformed": sum(1 for e in turns if e.get("kind") == "malformed"),
            "action_errors": sum(1 for e in turns if e.get("kind") == "action_error"), "truncated": sum(1 for e in turns if e.get("kind") == "truncated"),
            "schema_failure_rate": round(sum(1 for e in turns if e.get("kind") == "malformed") / len(turns), 3),
            "prompt_ms": _stat([e["prompt_ms"] for e in turns if e.get("prompt_ms") is not None]), "gen_ms": _stat([e["gen_ms"] for e in turns if e.get("gen_ms") is not None]),
            "load_ms_total": sum(e.get("load_ms") or 0 for e in turns), "wall_s": _stat([e["secs"] for e in turns if e.get("secs") is not None]),
            "gen_tok_per_s": round(statistics.mean(e["gtok"] / (e["gen_ms"] / 1000) for e in gen), 2) if gen else None,
            "evaluated_prompt_tokens": {"first_turn_mean": round(statistics.mean(e["ptok"] for e in first)) if first else None, "later_turn_mean": round(statistics.mean(e["ptok"] for e in later)) if later else None},
            "cold_prompt_tok_per_s": round(cold_tps, 1) if cold_tps else None, "cache_reuse_first_turn": reuse(first), "cache_reuse_later_turns": reuse(later),
            "max_est_prompt_tokens": max((e.get("est_prompt") or 0) for e in turns), "num_ctx": next((e["num_ctx"] for e in turns if e.get("num_ctx")), None),
            "truncated_contexts": sum(e.get("truncated", 0) for e in ends), "schema_fallbacks": sum(1 for e in trace if e.get("event") == "schema_fallback")}

def collect(proj):
    store = T.TaskStore(proj)
    if not store.exists(): return {"error": "not frozen"}
    data = store.load(); tasks = data["tasks"]; atts = [a for a in _jsonl(proj.p("attempts.jsonl")) if "attempt" in a]; ev = _jsonl(proj.p("attempts.jsonl"))
    plan_log = _jsonl(proj.p("plan.log")); trace = _jsonl(proj.p("trace.jsonl")); therm = _jsonl(proj.p("thermal.jsonl"))
    turns_by_task = {}
    for e in trace:
        if e.get("event") == "turn": turns_by_task[e["task"]] = turns_by_task.get(e["task"], 0) + 1
    per = []
    for t in tasks:
        mine = [a for a in atts if a["task"] == t["id"]]; secs = sum((a.get("worker_secs") or 0) + (a.get("secs") or 0) for a in mine)
        per.append({"id": t["id"], "title": t["title"][:70], "status": t["status"], "parent": t.get("parent"), "attempts": t["attempts"], "max_rung": max([a.get("rung", 1) for a in mine] or [1]),
                    "wall_s": round(secs), "turns": turns_by_task.get(t["id"], 0), "blocked_question": t.get("blocked_question")})
    original = [p for p in per if not p["parent"]]; done = [p for p in original if p["status"] == "done"]
    rej = [l for l in plan_log if l.get("op") == "check_rejected"]
    return {"project": proj.name, "collected": now(), "tasks": per,
            "pass_rate": {"done": len(done), "total": len(original), "rate": round(len(done) / len(original), 2) if original else None},
            "attempts_per_task": round(statistics.mean(p["attempts"] for p in original), 2) if original else None,
            "wall_s_per_task": round(statistics.mean(p["wall_s"] for p in original)) if original else None,
            "turns": summarize_turns(trace),
            "guard": {"rejected_total": len(rej), "by_actor": {a: sum(1 for r in rej if r.get("actor") == a) for a in {r.get("actor") for r in rej}}, "verdicts": {v: sum(1 for r in rej if r.get("verdict") == v) for v in {r.get("verdict") for r in rej}},
                      "details": [f"{r.get('task')}: {r.get('verdict')} ({r.get('actor')})" for r in rej][:20]},
            "ladder": {"escalations": [l["reason"] for l in plan_log if l.get("op") == "escalate"], "blocks": [l["reason"] for l in plan_log if l.get("op") == "block"],
                       "model_replans": [f"{l.get('op')}:{'ok' if l.get('ok') else l.get('error')}" for l in plan_log if l.get("actor") == "model"], "splits": [t["id"] for t in tasks if t.get("split_into")]},
            "tamper": sum(1 for e in ev if e.get("event") == "tamper"), "interrupted": sum(1 for e in ev if e.get("event") == "interrupted"),
            "thermal_pauses": sum(1 for e in therm if e.get("event") == "pause"), "thermal_wait_s": sum(e.get("waited", 0) for e in therm if e.get("event") == "resume")}

def render_md(res, run, plan_info=None):
    r = res; t = r["turns"]; L = [f"### Bench run `{r['project']}` ({run['status']}, {run['minutes']} min wall)", ""]
    if plan_info: L += [f"Planner (unattended, from the spec): {plan_info}", ""]
    pr = r["pass_rate"]; L += [f"- **Pass rate:** {pr['done']}/{pr['total']} original tasks verified ({int(100 * (pr['rate'] or 0))}%). Attempts per task: {r['attempts_per_task']}. Wall time per task: {r['wall_s_per_task']} s (worker + checks).", ""]
    L += ["| task | status | attempts | top rung | turns | wall s |", "|---|---|---|---|---|---|"] + [f"| {p['id']} {p['title'][:48]} | {p['status']} | {p['attempts']} | {p['max_rung']} | {p['turns']} | {p['wall_s']} |" for p in r["tasks"]] + [""]
    if t.get("turns"):
        L += [f"- **Per turn** ({t['turns']} model turns): prompt processing mean {t['prompt_ms']['mean']} ms (p50 {t['prompt_ms']['p50']}, p95 {t['prompt_ms']['p95']}); generation mean {t['gen_ms']['mean']} ms (p50 {t['gen_ms']['p50']}, p95 {t['gen_ms']['p95']}); wall mean {t['wall_s']['mean']} s; model load total {t['load_ms_total']} ms.",
              f"- **Speed:** generation {t['gen_tok_per_s']} tok/s; cold prompt processing {t['cold_prompt_tok_per_s']} tok/s (first turn of an attempt; later turns only evaluate what is new).",
              f"- **KV cache reuse:** first turn of an attempt {t['cache_reuse_first_turn']}, later turns {t['cache_reuse_later_turns']} (fraction of the prompt NOT re-evaluated, from prompt-processing time vs a cold {t['cold_prompt_tok_per_s']} tok/s; prompt tokens first/later: {t['evaluated_prompt_tokens']}).",
              f"- **Schema:** {t['malformed']} malformed of {t['turns']} replies ({int(100 * t['schema_failure_rate'])}% failure; grammar fallbacks to the flat schema: {t['schema_fallbacks']}); {t['truncated']} replies cut off by the token limit; {t['action_errors']} valid-JSON actions refused by validation/policy.",
              f"- **Context:** largest prompt estimated {t['max_est_prompt_tokens']} tokens of {t['num_ctx']}; {t['truncated_contexts']} truncations."]
    g = r["guard"]; L += [f"- **Baseline guard:** {g['rejected_total']} checks rejected {g['by_actor'] or ''} {g['verdicts'] or ''} {g['details'] or ''}"]
    lad = r["ladder"]; L += [f"- **Ladder steps fired ({len(lad['escalations'])}):** " + ("; ".join(lad["escalations"]) or "none"), f"- **Blocked:** " + ("; ".join(f"{p['id']}: {p['blocked_question']}" for p in r["tasks"] if p["status"] == "blocked") or "none"),
          f"- **Model replans:** {lad['model_replans'] or 'none'}; splits: {lad['splits'] or 'none'}; tamper caught: {r['tamper']}; interrupted: {r['interrupted']}.",
          f"- **Heat:** CPU package max {max(run['temps']) if run['temps'] else '?'} C, mean {round(statistics.mean(run['temps']), 1) if run['temps'] else '?'} C; thermal pauses {r['thermal_pauses']} ({r['thermal_wait_s']} s waiting)."]
    return "\n".join(L)

def main(argv):
    import argparse
    from . import preflight
    ap = argparse.ArgumentParser(prog="codebot bench"); ap.add_argument("--home"); ap.add_argument("--suite", default="pyutils"); ap.add_argument("--plan", choices=["fixed", "auto"], default="fixed")
    ap.add_argument("--preflight", action="store_true"); ap.add_argument("--max-minutes", type=int, default=150); ap.add_argument("--plan-minutes", type=int, default=45)
    ap.add_argument("--thermal", action="store_true", help="sweep thread/power/GPU configs for sustained speed vs heat"); ap.add_argument("--minutes", type=float, default=4.0)
    ap.add_argument("--only", help="comma separated substrings of config names to run (thermal sweep)")
    ap.add_argument("--name"); ap.add_argument("--record", action="store_true", help="append the result to docs/ROADMAP.md"); ap.add_argument("--collect-only")
    a = ap.parse_args(argv); from .config import home_dir; home = a.home or home_dir()
    if a.preflight:
        probe = Project(next(iter(sorted(os.listdir(os.path.join(home, "projects")))), "x"), home) if os.path.isdir(os.path.join(home, "projects")) and os.listdir(os.path.join(home, "projects")) else None
        class P:   # preflight only needs config values: read them like a project would, without needing a project
            def __init__(s): s.home, s.cfg, s.name, s.mode, s.rundir, s.state = home, read_env(os.path.join(home, "config", "codebot.env")), "preflight", "code", home, home
            def get(s, k, d=None, cast=str):
                v = os.environ.get(k, s.cfg.get(k)); 
                try: return cast(v) if v not in (None, "") else d
                except (TypeError, ValueError): return d
            def p(s, *x): return os.path.join(home, "logs", *x)
        os.makedirs(os.path.join(home, "logs"), exist_ok=True)
        items = preflight.run(P(), home); print(preflight.render(items)); return 1 if any(s == "FAIL" for _, s, _ in items) else 0
    if a.thermal:
        from . import thermalbench as TB
        class P:
            def __init__(s): s.home, s.cfg, s.name, s.mode, s.rundir, s.state = home, read_env(os.path.join(home, "config", "codebot.env")), "thermal", "code", home, home
            def get(s, k, d=None, cast=str):
                v = os.environ.get(k, s.cfg.get(k))
                try: return cast(v) if v not in (None, "") else d
                except (TypeError, ValueError): return d
            def p(s, *x): return os.path.join(home, "logs", *x)
        os.environ["THERMAL_GUARD"] = "0"; os.makedirs(os.path.join(home, "logs"), exist_ok=True); res = []
        cfgs = [c for c in TB.configs() if not a.only or any(o in c["name"] for o in a.only.split(","))]
        for c in cfgs:
            print(f"== {c['name']}", flush=True); r = TB.run_config(P(), c, a.minutes); res.append(r)
            print("   skipped: " + r["skipped"] if r.get("skipped") else f"   steady {r['steady']}", flush=True)
        md = TB.render(res, a.minutes); print("\n" + md)
        os.makedirs(os.path.join(home, "bench", "results"), exist_ok=True)
        with open(os.path.join(home, "bench", "results", f"thermal-{time.strftime('%m%d-%H%M%S')}.json"), "w") as f: json.dump(res, f, indent=1)
        if a.record:
            with open(os.path.join(home, "docs", "ROADMAP.md"), "a") as f: f.write("\n" + md + "\n")
        return 0
    if a.collect_only:
        proj = Project(a.collect_only, home); res = collect(proj); print(json.dumps(res, indent=1)); return 0
    name = setup(home, a.suite, a.plan, a.name); plan_info = None; print(f"project {name}")
    if a.plan == "auto":
        print("planner running (unattended, harness-driven repair rounds)...", flush=True); pi = plan_auto(home, name, a.plan_minutes * 60)
        plan_info = (f"{len(pi['rounds'])} round(s), {pi['secs']} s, final check-plan {'OK' if pi['ok'] else 'REJECTED'}. " + "; ".join(
            f"round {r['n']}: rejected {r['rejected'] or 'none'}, violations {r['violations'] or 'none'}" for r in pi["rounds"])); print(plan_info, flush=True)
        if not os.path.exists(os.path.join(home, "projects", name, "PLAN.md")):
            print("the planner wrote no PLAN.md; nothing to run"); return 2
    run = run_project(home, name, a.max_minutes); proj = Project(name, home)
    res = collect(proj) if run["status"] != "freeze_failed" else {"error": run["detail"]}
    out = os.path.join(home, "bench", "results"); os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, f"{name}.json"), "w") as f: json.dump({"run": run, "result": res, "plan": plan_info}, f, indent=1)
    if "error" in res:
        md = f"### Bench run `{name}`: {run['status']}\n\n{run.get('detail', res['error'])}\n"
        rej = [l for l in _jsonl(proj.p("plan.log")) if l.get("op") == "check_rejected"]; md += f"\nBaseline guard rejections logged: {len(rej)}\n"
    else: md = render_md(res, run, plan_info)
    print("\n" + md)
    if a.record:
        with open(os.path.join(home, "docs", "ROADMAP.md"), "a") as f: f.write("\n" + md + "\n")
    return 0 if run["status"] == "finished" else 1
