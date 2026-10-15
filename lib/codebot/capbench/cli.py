"""`codebot bench list | run | status | report | resume | cancel | validate | coverage`. The older flag-style invocations of `codebot bench` are untouched
(bench.main dispatches here only when the first word is one of SUBCOMMANDS)."""
import argparse, json, os, sys, time
from . import manifests as M, notify as NT, reference as R, reports, results as RS, runner as RN

SUBCOMMANDS = ("list", "run", "status", "report", "resume", "cancel", "validate", "coverage")

def _store(home, ident=None): return RS.Store(home, RS.resolve(home, ident))

def cmd_list(home, a):
    print(f"{'level':<8}{'tasks':<7}{'reqs':<6}{'ref plan':<10}title (primary challenge)")
    for m in M.levels(home):
        print(f"{m['level']:<8}{m['expected_tasks']:<7}{len(m['requirements']):<6}{'yes' if M.plan_path(m) else 'no':<10}{m['title']} ({m['primary_challenge']})")
    try:
        g = M.load(home, "growing"); print(f"{'growing':<8}{sum(s['expected_tasks'] for s in g['stages']):<7}{sum(len(s['requirements']) for s in g['stages']):<6}{'-':<10}{g['title']} ({len(g['stages'])} stages: " + ", ".join(s['id'] for s in g['stages']) + ")")
    except M.CatalogError: pass
    print("\nrun one:    codebot bench run --level 1\nfull ladder: codebot bench run --suite ladder --repeats 3\ngrowing:     codebot bench run --suite growing")
    return 0

def cmd_run(home, a):
    suite = "growing" if a.suite == "growing" else "ladder"
    if a.level: a.levels = a.level; suite = "ladder"
    try: levels = M.parse_levels(home, a.levels or "1-12") if suite == "ladder" else None
    except M.CatalogError as e: print(e); return 2
    repeats = a.repeats if a.repeats else (1 if a.level else 3)
    items = [M.load(home, f"level_{n:02d}") for n in levels] if levels else [M.load(home, "growing")]
    problems = RN.preflight(home, items, need_model=not a.dry_run, mode=a.mode, driver=a.driver, env_overrides_ok=a.allow_env_overrides)
    if problems: print("cannot start:\n  - " + "\n  - ".join(problems)); return 2
    try: store = RN.create_run(home, suite, levels, repeats, a.mode, a.task_minutes, a.plan_minutes, a.plan_rounds, a.model, a.seed, a.interactive, a.name, a.poll, not a.no_notify, a.order, a.driver, a.checkpoints)
    except RN.RunError as e: print(e); return 2
    n = len(store.load()["trials"])
    print(f"run {store.run_id}: {n} trial(s), mode {a.mode}, {a.task_minutes:g} min per task (no limit on the project as a whole)")
    if a.dry_run: print("dry run: nothing started; state is in " + store.dir); return 0
    if a.foreground: return _go(home, store.run_id)
    pid = RN.start_detached(home, store.run_id); print(f"running in the background (pid {pid}). progress: codebot bench status {store.run_id}   report: codebot bench report {store.run_id}   stop: codebot bench cancel {store.run_id}"); return 0

def _go(home, run_id):
    store = RS.Store(home, run_id); st = store.load(); c = st["config"]
    ctx = RN.Ctx(home, store, st, NT.Notifier(home, enabled=c.get("notify", True)), driver=c.get("driver", "loop"), poll_s=c.get("poll_s", 20.0))
    status = RN.execute(ctx); print(f"run {run_id}: {status}"); print(reports.one_line(store)); return 0 if status in ("finished", "cancelled") else 1

def cmd_resume(home, a):
    store = _store(home, a.run); st = store.load()
    if store.runner_alive() and not a.foreground: print(f"{store.run_id} is already running (pid {open(store.pid_path()).read().strip()})"); return 0
    if st["status"] == "finished": print(f"{store.run_id} already finished"); return 0
    unfinished = sum(1 for t in st["trials"] if t["phase"] != "finished"); print(f"resuming {store.run_id}: {unfinished} unfinished trial(s); finished trials and consumed task time are kept")
    if a.foreground: return _go(home, store.run_id)
    pid = RN.start_detached(home, store.run_id); print(f"running in the background (pid {pid})"); return 0

def _status_line(t):
    p = t.get("progress") or {}; c = p.get("counts") or {}
    extra = f"  tasks {c.get('done', 0)} done/{c.get('pending', 0)} pending/{c.get('timeout', 0)} timeout/{c.get('blocked', 0)} blocked, {p.get('temp')} C" if c and t["phase"] == "running" else ""
    res = f"  -> {t.get('outcome')}" if t["phase"] == "finished" else ""
    return f"  {t['trial_id']:<12}{t['phase']:<10}{extra}{res}"

def cmd_status(home, a):
    runs = RS.list_runs(home)
    if not runs: print("no benchmark runs yet"); return 0
    if not a.run:
        print(f"{'run':<34}{'status':<11}{'trials':<10}{'mode':<16}model")
        for r in runs[:15]:
            s = RS.Store(home, r).load(); fin = sum(1 for t in s["trials"] if t["phase"] == "finished")
            live = "running" if s["status"] == "running" and RS.Store(home, r).runner_alive() else ("STALLED" if s["status"] == "running" else s["status"])
            print(f"{r:<34}{live:<11}{fin}/{len(s['trials']):<7}{s['mode']:<16}{s['config']['model']}")
        return 0
    store = _store(home, a.run); st = store.load(); alive = store.runner_alive()
    state = st["status"] + ("" if st["status"] != "running" else (" (runner alive)" if alive else " (RUNNER NOT ALIVE: codebot bench resume " + store.run_id + ")"))
    print(f"run {store.run_id}: {state}   mode {st['mode']}   {st['config']['task_minutes']:g} min per task   updated {st['updated']}")
    for t in store.all_trials(): print(_status_line(t))
    return 0

def cmd_report(home, a):
    if a.history: print(reports.history_table(home, a.level_filter)); return 0
    if a.compare: print(reports.compare(home, RS.resolve(home, a.compare[0]), RS.resolve(home, a.compare[1]))); return 0
    store = _store(home, a.run)
    if a.reclassify: _reclassify(home, store)
    rep = reports.write_all(store)
    if a.json: print(json.dumps(rep, indent=1)); return 0
    print(reports.markdown(rep, store)); print(f"(files: {store.dir}/report.md report.json trials.csv tasks.csv calls.csv)"); return 0

def _reclassify(home, store):
    from . import classify as CL
    for t in store.all_trials():
        if t.get("phase") != "finished" or not t.get("tasks"): continue
        m = M.load(home, t["benchmark"]); st = m["stages"][t["stage"] - 1] if t.get("stage") else None
        reqs = (st or m)["requirements"] if not st else [{**r, "id": f"{st['id']}:{r['id']}"} for s in m["stages"][:t["stage"]] for r in s["requirements"]]
        d = store.load_trial(t["trial_id"]); d["classification"] = CL.summarize(d, reqs); store.save_trial(t["trial_id"], d)

def cmd_cancel(home, a):
    store = _store(home, a.run); store.request_cancel(); print(f"cancel requested for {store.run_id}: the runner stops the current trial's loop at its next poll (resume later with codebot bench resume {store.run_id})"); return 0

def cmd_validate(home, a):
    """Self-test of the benchmark itself: every reference test passes on the reference solution and not on the bare seed. No model needed."""
    bad = 0
    for m in ([M.load(home, n) for n in M.parse_levels(home, a.levels or "1-12")] + ([M.load(home, "growing")] if a.levels in (None, "growing") and os.path.isdir(os.path.join(M.catalog_dir(home), "growing")) else [])):
        r = R.validate_level(m, home); s = r.get("solution") or {}
        tag = "OK " if not r["problems"] else "BAD"; bad += 1 if r["problems"] else 0
        print(f"{tag} {m['_id']:<10} solution {s.get('passed', '-')}/{s.get('total', '-')}  seed {(r.get('seed') or {}).get('passed', '-')}/{(r.get('seed') or {}).get('total', '-')}")
        for p in r["problems"]: print("      " + p)
        if M.plan_path(m):
            pp = R.validate_plan(m, home); bad += 1 if pp else 0; print(f"    reference plan: {'validated' if not pp else 'PROBLEMS'}")
            for p in pp: print("      " + p)
    return 1 if bad else 0

def cmd_coverage(home, a):
    """Which reference tests cover which requirement (generated from the manifests and the tests themselves, so it cannot drift)."""
    for m in ([M.load(home, n) for n in M.parse_levels(home, a.levels or "1-12")]):
        import re
        counts = {}
        td = M.tests_dir(m)
        for fn in sorted(os.listdir(td)):
            if fn.startswith("test_") and fn.endswith(".py"):
                for tid in R._declared(td, fn): r = R.req_of(tid) or "unmapped"; counts[r] = counts.get(r, 0) + 1
        print(f"## {m['_id']} {m['title']}")
        for r in m["requirements"]: print(f"  {r['id']:<4}{counts.get(r['id'], 0):>3} tests  {r['title']}")
        if counts.get("unmapped"): print(f"  unmapped {counts['unmapped']}")
    return 0

def main(argv, home):
    ap = argparse.ArgumentParser(prog="codebot bench")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="the catalog")
    r = sub.add_parser("run", help="start a benchmark run (detached unless --foreground)")
    r.add_argument("--level", help="one level, e.g. 1 (repeats default to 1)"); r.add_argument("--levels", help="e.g. 1-12, 3,5 (default 1-12)"); r.add_argument("--suite", choices=["ladder", "growing"], default="ladder")
    r.add_argument("--repeats", type=int, help="trials per level (default 3 for a suite, 1 for --level)"); r.add_argument("--mode", choices=list(RN.MODES), default="full", help="full | reference-plan | minimal (diagnostics)")
    r.add_argument("--task-minutes", type=float, default=90.0, help="wall-clock budget of EACH planned task (default 90); there is no project-wide limit"); r.add_argument("--plan-minutes", type=float, default=45.0)
    r.add_argument("--plan-rounds", type=int, default=3); r.add_argument("--model"); r.add_argument("--seed", type=int); r.add_argument("--name"); r.add_argument("--poll", type=float, default=20.0)
    r.add_argument("--interactive", action="store_true", help="a blocked task waits for `codebot answer` instead of being a terminal outcome"); r.add_argument("--no-notify", action="store_true")
    r.add_argument("--order", choices=["repeat-major", "level-major"], default="repeat-major"); r.add_argument("--driver", choices=["loop", "engine"], default="loop", help="loop = the real bash supervisor (unattended); engine = in-process steps")
    r.add_argument("--checkpoints", action="store_true", help="also score the independent tests after every verified task"); r.add_argument("--foreground", action="store_true"); r.add_argument("--dry-run", action="store_true")
    r.add_argument("--allow-env-overrides", action="store_true", help="expert/test use: do not refuse to start when SANDBOX, WORKER_CMD, TASK_MAX_MINUTES ... are exported in the environment")
    s = sub.add_parser("status"); s.add_argument("run", nargs="?")
    rp = sub.add_parser("report"); rp.add_argument("run", nargs="?"); rp.add_argument("--json", action="store_true"); rp.add_argument("--history", action="store_true"); rp.add_argument("--level-filter", type=int, dest="level_filter")
    rp.add_argument("--compare", nargs=2, metavar="RUN"); rp.add_argument("--reclassify", action="store_true")
    rs = sub.add_parser("resume"); rs.add_argument("run", nargs="?"); rs.add_argument("--foreground", action="store_true")
    c = sub.add_parser("cancel"); c.add_argument("run", nargs="?")
    v = sub.add_parser("validate", help="self-test the catalog's oracles (no model)"); v.add_argument("--levels")
    cv = sub.add_parser("coverage"); cv.add_argument("--levels")
    a = ap.parse_args(argv); home = os.path.abspath(home)
    return {"list": cmd_list, "run": cmd_run, "status": cmd_status, "report": cmd_report, "resume": cmd_resume, "cancel": cmd_cancel, "validate": cmd_validate, "coverage": cmd_coverage}[a.cmd](home, a)
