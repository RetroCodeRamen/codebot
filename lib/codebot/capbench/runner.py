"""Trial and run execution. A RUN is an ordered list of TRIALS (one level x one repeat, or one growing-project stage). Every trial passes through
persisted phases, so a killed runner resumes exactly where it stopped without repeating finished work and without resetting any task's time budget
(the budgets live in the engine, lib/codebot/taskclock.py; this module never touches them):

  created -> planning -> planned -> frozen -> running -> loop_done -> finished

Modes (what is being measured):
  full            planner (from the spec alone) + worker + recovery ladder: the normal benchmark
  reference-plan  the validated catalog plan replaces the planner (isolates planning)
  minimal         one task = the whole spec, no acceptance check, no recovery ladder (isolates the model + bare coding loop)"""
import fcntl, json, os, shutil, subprocess, sys, tempfile, time, urllib.request
from .. import bench, checkspec as K, engine, guard, planner as PL, planparse, sandbox, taskclock as TC, tasks as T, thermal
from ..config import Project, read_env, home_env
from . import classify as CL, manifests as M, metrics as MT, notify as NT, reference as R, results as RS, workspace as W

MODES = ("full", "reference-plan", "minimal")
TERMINAL_EVENTS = ("all_done", "finished_incomplete", "done_with_blocked", "stalled", "budget", "error")

class RunError(Exception):
    pass

class Cfg:
    """Minimal project-like object for checks that happen before a trial project exists (it deliberately ignores the process environment)."""
    def __init__(self, home, **extra):
        self.home, self.name, self.mode, self.rundir, self.state = home, "capbench", "code", home, home
        self.cfg = home_env(home); self.cfg.update({k: str(v) for k, v in extra.items()})
    def get(self, k, d=None, cast=str):
        v = self.cfg.get(k)
        try: return cast(v) if v not in (None, "") else d
        except (TypeError, ValueError): return d
    def p(self, *x): return os.path.join(self.home, "logs", *x)

INTEGRITY_ENV = ("SANDBOX", "TASK_MAX_MINUTES", "BLOCK_TERMINAL", "MAX_ITERATIONS", "PROJECT_MAX_MINUTES", "WORKER_CMD", "WORKER", "SPLIT_CMD", "CHECK_BASELINE_GUARD", "REGRESSION_GATE", "PLAN_PER_ITEM", "PLANNER_MODEL", "PLANNER_THINK", "SPLITTER_MODEL", "SPLITTER_THINK", "WORKER_THINK", "ESCALATE_MODEL", "THINK_BUDGET", "LADDER_TRIES", "ALLOW_UNCHECKED", "CODEBOT_MODEL")

class Ctx:
    """Everything a trial needs; tests replace `sleep`, `sender` and `driver` to run without real time or a real model."""
    def __init__(self, home, store, state, notifier=None, driver="loop", sleep=time.sleep, log=None, poll_s=20.0, model_check=True):
        self.home, self.store, self.state, self.cfg = home, store, state, state["config"]
        self.notifier = notifier or NT.Notifier(home, enabled=False); self.driver, self.sleep, self.poll_s, self.model_check = driver, sleep, poll_s, model_check
        self._log = log; self.logfile = os.path.join(store.dir, "runner.log")
    def log(self, msg):
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        os.makedirs(os.path.dirname(self.logfile), exist_ok=True)
        with open(self.logfile, "a") as f: f.write(line + "\n")
        (self._log or print)(line)

# ---------- infrastructure ----------
def model_up(home, port=None):
    port = port or home_env(home).get("OLLAMA_PORT", "11500")
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/tags", timeout=5) as r: return r.status == 200
    except OSError: return False

def ensure_model_server(ctx):
    if not ctx.model_check or model_up(ctx.home, ctx.cfg.get("ollama_port")): return True
    subprocess.run([os.path.join(ctx.home, "bin", "codebot"), "setup"], capture_output=True, timeout=180, stdin=subprocess.DEVNULL)
    for _ in range(20):
        if model_up(ctx.home, ctx.cfg.get("ollama_port")): return True
        ctx.sleep(3)
    return False

def unload_model(home, model=None, port=None):
    """Free RAM/heat when the benchmark is idle (keep_alive 0 on the model)."""
    cfg = home_env(home); port = port or cfg.get("OLLAMA_PORT", "11500"); model = model or cfg.get("CODEBOT_MODEL", "codebot-model")
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/generate", data=json.dumps({"model": model, "keep_alive": 0}).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r: r.read()
    except OSError: pass

def preflight(home, levels_or_ids, need_model=True, mode="full", driver="loop", env_overrides_ok=False):
    """Problems that must stop a run BEFORE anything is created. Empty = go."""
    p = []
    if not env_overrides_ok:    # codebot settings read the environment first, so a stray `export SANDBOX=none` would silently change what the benchmark measures
        hit = [k for k in INTEGRITY_ENV if os.environ.get(k)]
        if hit: p.append("these environment variables override benchmark settings and would change what is measured: " + ", ".join(hit) + " (unset them)")
    try: sandbox.mode(Cfg(home, SANDBOX="docker"))
    except RuntimeError as e: p.append(f"the Docker sandbox is required (it keeps the hidden reference tests out of the worker's reach): {e}")
    if need_model and not model_up(home): p.append("the local model server is not reachable (codebot setup starts it)")
    if shutil.disk_usage(home).free < 2 * 1024 ** 3: p.append("less than 2 GB of free disk space")
    for m in levels_or_ids:
        bad = M.validate_manifest(m, home)
        if bad: p.append(f"{m['_id']}: " + "; ".join(bad[:3]))
        if mode == "reference-plan" and not m.get("stages") and not M.plan_path(m): p.append(f"{m['_id']}: no reference plan in the catalog (reference-plan mode is available for: " + ", ".join(x["_id"] for x in M.levels(home) if M.plan_path(x)) + ")")
    return p

class model_lock:
    """The same lock file the bash supervisor uses (flock logs/model.lock): one model job at a time, planner included."""
    def __init__(self, home): os.makedirs(os.path.join(home, "logs"), exist_ok=True); self.path = os.path.join(home, "logs", "model.lock")
    def __enter__(self): self.f = open(self.path, "a"); fcntl.flock(self.f, fcntl.LOCK_EX); return self
    def __exit__(self, *a): fcntl.flock(self.f, fcntl.LOCK_UN); self.f.close()

# ---------- run construction ----------
def trial_id(kind, level, repeat, stage=None):
    if kind.startswith("bench:"): return f"{kind[6:]}-r{repeat}"                      # a named catalog entry (e.g. calc_go) outside the numbered ladder
    return f"G-r{repeat}-s{stage}" if kind == "growing" else f"L{level:02d}-r{repeat}"

def build_trials(home, suite, levels, repeats, order="repeat-major"):
    out = []
    if suite == "growing":
        m = M.load(home, "growing")
        for r in range(1, repeats + 1):
            for i, st in enumerate(m["stages"], 1): out.append({"trial_id": trial_id("growing", None, r, i), "benchmark": "growing", "level": None, "stage": i, "stage_id": st["id"], "repeat": r, "phase": "created", "outcome": None})
        return out
    if suite.startswith("bench:"):
        return [{"trial_id": trial_id(suite, None, r), "benchmark": suite[6:], "level": None, "stage": None, "repeat": r, "phase": "created", "outcome": None} for r in range(1, repeats + 1)]
    pairs = [(r, lv) for r in range(1, repeats + 1) for lv in levels] if order == "repeat-major" else [(r, lv) for lv in levels for r in range(1, repeats + 1)]
    for r, lv in pairs: out.append({"trial_id": trial_id("ladder", lv, r), "benchmark": f"level_{lv:02d}", "level": lv, "stage": None, "repeat": r, "phase": "created", "outcome": None})
    return out

def create_run(home, suite="ladder", levels=None, repeats=3, mode="full", task_minutes=90.0, plan_minutes=45.0, plan_rounds=3, model=None, seed=None, interactive=False,
               name=None, poll_s=20.0, notify=True, order="repeat-major", driver="loop", checkpoints=False, roles=None):
    """roles: per-role settings written into every trial's project.env, e.g. {"PLANNER_MODEL": "granite4.2:8b-q4_K_M", "PLANNER_THINK": "on"} (roles.py)."""
    if mode not in MODES: raise RunError(f"mode must be one of {', '.join(MODES)}")
    if suite == "growing" and mode != "full": raise RunError("the growing project is only available in full mode")
    if suite == "ladder" and not levels: raise RunError("no levels selected")
    trials = build_trials(home, suite, levels or [], repeats, order)
    run_id = RS.new_run_id(name); store = RS.Store(home, run_id)
    if store.exists(): raise RunError(f"run {run_id} already exists")
    env = RS.environment(home, model=model)
    state = {"run_id": run_id, "created": RS.now_iso(), "status": "created", "suite": suite, "mode": mode, "levels": levels, "repeats": repeats,
             "config": {"task_minutes": task_minutes, "plan_minutes": plan_minutes, "plan_rounds": plan_rounds, "model": model or env["model"], "seed": seed, "interactive": interactive, "poll_s": poll_s,
                        "notify": notify, "driver": driver, "checkpoints": checkpoints, "ollama_port": env["ollama_port"], "order": order,
                        "roles": {k: str(v) for k, v in (roles or {}).items() if v not in (None, "")}},
             "environment": env, "trials": trials}
    store.save(state); return store

# ---------- phases ----------
def _set_phase(ctx, tr, td, phase, **kw):
    tr["phase"] = phase; td["phase"] = phase; td.update(kw)
    def f(st):
        for t in st["trials"]:
            if t["trial_id"] == tr["trial_id"]: t.update({k: tr[k] for k in ("phase", "outcome", "project", "started", "finished") if k in tr})
    ctx.store.update(f); ctx.store.save_trial(tr["trial_id"], td)

def _benchmark(ctx, tr):
    m = M.load(ctx.home, tr["benchmark"]); st = m["stages"][tr["stage"] - 1] if tr.get("stage") else None; return m, st

def _prev_project(ctx, tr):
    """The project of the previous stage of this repeat (growing benchmark), or None."""
    if not tr.get("stage") or tr["stage"] == 1: return None
    want = trial_id("growing", None, tr["repeat"], tr["stage"] - 1)
    for t in ctx.state["trials"]:
        if t["trial_id"] == want: return Project(t["project"], ctx.home) if t.get("project") else None
    return None

def _seed_dirs(ctx, tr, m, st):
    prev = _prev_project(ctx, tr)
    if st and prev: return [W.code_dir(prev)]
    return [M.seed_dir(m)]

def _spec_for(ctx, tr, m, st):
    return M.spec_text(m, st)

def _cfg_for(ctx, tr, minimal=False, m=None):
    c = ctx.cfg; extra = dict(c.get("roles") or {})
    sb = (m or {}).get("sandbox") or {}                   # a catalog entry may need a toolchain in the sandbox (calc_go: Go on PATH, a build cache in the code dir)
    if sb.get("path_prepend"): extra["SANDBOX_PATH"] = sb["path_prepend"]
    if sb.get("env"): extra["SANDBOX_SETENV"] = ";".join(f"{k}={v}" for k, v in sb["env"].items())
    return W.project_settings(ctx.home, c["task_minutes"], c.get("model"), c.get("seed"), c.get("interactive"), extra=extra or None, minimal=minimal)

def setup_trial(ctx, tr, td):
    m, st = _benchmark(ctx, tr); minimal = ctx.state["mode"] == "minimal"
    name = W.names(ctx.state["run_id"], tr["trial_id"]); W.destroy(ctx.home, name)          # a re-planned trial starts clean: planning keeps no state worth resuming
    t0 = time.time(); spec = _spec_for(ctx, tr, m, st)
    proj = W.create(ctx.home, name, _seed_dirs(ctx, tr, m, st), spec, _cfg_for(ctx, tr, minimal, m))
    if minimal:     # the minimal arm has no planner: the worker gets the spec as a file in its own code dir, and ONE task
        with open(os.path.join(proj.workdir, "SPEC.md"), "w") as f: f.write(spec)
        bench._git(proj.workdir, "add", "-A"); bench._git(proj.workdir, "commit", "-qm", "spec")
    tr["project"] = name; td["timing"] = {"setup_s": round(time.time() - t0, 2)}
    return proj

def add_regression_gate(ctx, proj, tr):
    """Growing project: earlier stages' frozen assertion specs stay in force. They become one more task at the end of this stage's plan, whose check is
    the whole earlier suite (it holds today, so the baseline guard is skipped for the freeze that follows)."""
    prev = _prev_project(ctx, tr); copied = 0
    if prev is None: return 0
    pcd, cd = prev.p("checks"), proj.p("checks"); os.makedirs(cd, exist_ok=True); tag = f"S{tr['stage'] - 1}"
    for fn in sorted(os.listdir(pcd)) if os.path.isdir(pcd) else []:
        if fn.endswith(".spec.json") and not fn.startswith("S"): shutil.copyfile(os.path.join(pcd, fn), os.path.join(cd, f"{tag}_{fn}")); copied += 1
    for fn in sorted(os.listdir(pcd)) if os.path.isdir(pcd) else []:                    # specs carried through earlier gates stay in force too
        if fn.startswith("S") and fn.endswith(".spec.json"): shutil.copyfile(os.path.join(pcd, fn), os.path.join(cd, fn)); copied += 1
    if not copied: return 0
    with open(os.path.join(cd, "regress.sh"), "w") as f:
        f.write('#!/usr/bin/env bash\ncd "$CODEBOT_RUNDIR" || exit 99\nrc=0\nfor f in "$CODEBOT_CHECKS"/S*_T*.spec.json; do python3 "$CODEBOT_CHECKS/assert_runner.py" "$f" || rc=1; done\nexit $rc\n')
    with open(proj.p("PLAN.md")) as f: text = f.read()
    _, tasks = planparse.parse_plan(text); n = len(tasks)
    text = text.rstrip() + f"\n- [ ] {n + 1}. Keep every earlier stage working (regression gate over {copied} earlier assertion files)\n  - check: `bash $CODEBOT_CHECKS/regress.sh`\n  - size: S\n  - deps: {n}\n"
    with open(proj.p("PLAN.md"), "w") as f: f.write(text)
    return copied

def plan_trial(ctx, tr, td, proj):
    m, st = _benchmark(ctx, tr); mode = ctx.state["mode"]; spec = _spec_for(ctx, tr, m, st); lim = (st or m)["planner"]
    info = {"mode": mode, "ok": False, "rounds": [], "secs": 0, "spec_items": PL.spec_items(spec), "tasks_planned": None}
    t0 = time.time(); start_iso = RS.now_iso()
    if mode == "full":
        with model_lock(ctx.home):
            pi = PL.plan_auto(ctx.home, proj.name, int(ctx.cfg["plan_minutes"] * 60), ctx.cfg["plan_rounds"], log=ctx.log, max_tasks=lim["max_tasks"], num_predict=lim.get("num_predict", 5000))
        info.update(pi); info["mode"] = "full"
    elif mode == "reference-plan":
        plan_file = M.plan_path(m, st)
        if not plan_file: raise RunError(f"{m['_id']} has no reference plan")
        with open(plan_file) as f: plan = json.load(f)
        rej = PL.install_plan(proj, plan); info.update({"ok": not rej, "rounds": [{"n": 1, "rejected": [f"{r['id']}:{r['verdict']}" for r in rej], "ok": not rej}], "reference_plan": os.path.relpath(plan_file, ctx.home)})
    else:
        K.ensure_runner(proj)
        title = "Implement the whole project described in SPEC.md (in the code directory); when finished, call finish"
        with open(proj.p("PLAN.md"), "w") as f:
            f.write("# Goal\nImplement SPEC.md.\n# Context\nPython 3, standard library only.\n# Definition of done\nThe worker says it is done (no internal acceptance check in this mode).\n# Tasks\n"
                    f"- [ ] 1. {title}\n  - size: L\n")
        info.update({"ok": True, "rounds": [{"n": 1, "rejected": [], "ok": True}]})
    info["secs"] = round(time.time() - t0, 1); info["window"] = [start_iso, RS.now_iso()]
    try:
        with open(proj.p("PLAN.md")) as f: _, tasks = planparse.parse_plan(f.read())
        info["tasks_planned"] = len(tasks); info["plan_titles"] = [t["title"][:100] for t in tasks]
    except OSError: info["tasks_planned"] = 0
    td["plan"] = info; return info

def freeze_trial(ctx, tr, td, proj):
    gate = add_regression_gate(ctx, proj, tr) if tr.get("stage") else 0
    if T.TaskStore(proj).exists(): return True
    try: engine.freeze_project(proj, allow_unchecked=ctx.state["mode"] == "minimal", allow_passing=bool(gate))
    except SystemExit as e: td["freeze_error"] = str(e)[:800]; return False
    td["regression_gate_files"] = gate; return True

# ---------- driving ----------
def _counts(proj):
    try: return T.counts(T.TaskStore(proj).load())
    except (OSError, ValueError): return None

def _task_states(proj):
    try: return {t["id"]: t["status"] for t in T.TaskStore(proj).load()["tasks"]}
    except (OSError, ValueError): return {}

def _loop_alive(proj):
    try:
        pid = int(RS.read_text(proj.p("loop.pid")).strip()); os.kill(pid, 0)
        return "__loop" in RS.read_text(f"/proc/{pid}/cmdline").replace("\0", " ")
    except (OSError, ValueError): return False

def _track(ctx, tr, td, proj, seen):
    """Notifications and progress for tasks that changed state since the last look. Returns the current states."""
    cur = _task_states(proj); lim = ctx.cfg["task_minutes"]
    for tid, s in cur.items():
        if seen.get(tid) != s:
            if s == "timeout" and "." not in tid:
                used = TC.used(proj, TC.root_of(tid)) / 60; ctx.notifier.timeout(ctx.state["run_id"], tr["trial_id"], tid, used, lim); ctx.log(f"{tr['trial_id']} {tid} TIMEOUT after {used:.0f} min")
            if s == "blocked" and ctx.cfg.get("interactive") and not seen.get(f"asked:{tid}"):
                seen[f"asked:{tid}"] = True; q = next((t.get("blocked_question") for t in T.TaskStore(proj).load()["tasks"] if t["id"] == tid), "")
                ctx.notifier.human(ctx.state["run_id"], tr["trial_id"], tid, q or "blocked"); ctx.log(f"{tr['trial_id']} {tid} BLOCKED, waiting for a human answer (interactive mode)")
    seen.update(cur); return cur

def _progress(ctx, tr, td, proj, t0, temps):
    t = thermal.read_temp()
    if t is not None: temps.append(round(t, 1)); del temps[:-500]
    c = _counts(proj); td["progress"] = {"updated": RS.now_iso(), "counts": c, "temp": t, "run_s": round(time.time() - t0)}; td["temps"] = temps[-200:]
    ctx.store.save_trial(tr["trial_id"], td); return c

def drive_loop(ctx, tr, td, proj):
    """The real unattended path: `codebot start` (bash supervisor: thermal guard, per-step flock, phone/ntfy hooks, watchdog restart) polled from here."""
    home, t0, temps, seen, restarts, settled = ctx.home, time.time(), td.get("temps", []), {}, 0, 0
    interactive = bool(ctx.cfg.get("interactive")); started = os.path.exists(proj.p(".git-prepped"))
    def start(keep):
        r = bench.codebot(home, "start", proj.name, *(["--keep"] if keep else []))
        if r.returncode != 0 and not _loop_alive(proj): raise RunError(f"codebot start failed: {(r.stdout + r.stderr).strip()[-400:]}")
    def stop():                    # confirm the loop is really gone: a stop that races a just-started loop finds no pid and leaves it running
        for _ in range(5):
            bench.codebot(home, "stop", proj.name)
            if not _loop_alive(proj): return
            time.sleep(1)
    if ctx.store.cancel_requested(): return "cancelled"
    if not _loop_alive(proj) and _counts(proj) and not (_counts(proj)["pending"] == 0 and _counts(proj)["running"] == 0): start(started)
    while True:
        if ctx.store.cancel_requested(): stop(); return "cancelled"
        ctx.sleep(ctx.poll_s); c = _progress(ctx, tr, td, proj, t0, temps); _track(ctx, tr, td, proj, seen)
        if c is None: continue
        busy = c["pending"] or c["running"] or (interactive and c["blocked"])
        settled = 0 if busy else settled + 1
        alive = _loop_alive(proj)
        if not busy and (settled >= 2 or not alive): break
        if not alive and busy:
            restarts += 1; td.setdefault("infra_events", []).append({"ts": RS.now_iso(), "event": "loop_died", "n": restarts})
            if restarts > 3: raise RunError("the supervisor loop died 4 times; giving up on this trial")
            ctx.notifier.critical(ctx.state["run_id"], f"{tr['trial_id']}: the supervisor loop died; restarting ({restarts}/3)"); ctx.log(f"loop died; restarting ({restarts})"); start(True)
        elif alive and ctx.model_check and not model_up(home, ctx.cfg.get("ollama_port")):
            td.setdefault("infra_events", []).append({"ts": RS.now_iso(), "event": "model_server_down"}); ctx.log("model server unreachable; pausing and restarting it")
            bench.codebot(home, "pause", proj.name, "--soft")
            if not ensure_model_server(ctx): ctx.notifier.critical(ctx.state["run_id"], f"{tr['trial_id']}: the model server is down and could not be restarted"); raise RunError("model server unavailable")
            bench.codebot(home, "resume", proj.name)
    bench.codebot(home, "stop", proj.name)
    return "settled"

def drive_engine(ctx, tr, td, proj):
    """Foreground driver: the same engine steps the supervisor runs, called in-process (used by tests and by `--driver engine`)."""
    t0, temps, seen = time.time(), td.get("temps", []), {}; last = None
    while True:
        if ctx.store.cancel_requested(): return "cancelled"
        with model_lock(ctx.home): ev = engine.step(proj)
        _progress(ctx, tr, td, proj, t0, temps); _track(ctx, tr, td, proj, seen); last = ev
        td.setdefault("events", []).append({k: v for k, v in ev.items() if k in ("event", "task", "root", "attempt")}); del td["events"][:-300]
        if ev["event"] == "error": td.setdefault("infra_events", []).append({"ts": RS.now_iso(), "event": "engine_error", "error": ev.get("error")}); raise RunError(ev.get("error", "engine error"))
        if ev["event"] in TERMINAL_EVENTS: return "settled"

# ---------- verification ----------
def snapshot(rundir, sha, dest):
    p = subprocess.run(f"git -C {rundir!r} archive {sha} | tar -x -C {dest!r}", shell=True, capture_output=True)
    return p.returncode == 0

def checkpoint_curve(ctx, tr, m, st, proj, tasks):
    """Reference pass rate after each verified root task (from the commit the engine recorded for it): where in the run the independent score was earned."""
    out = []; data = T.TaskStore(proj).load()["tasks"]; code = W.code_dir(proj)
    for t in tasks:
        if t["status"] != "VERIFIED": continue
        sha = next((x.get("commit") for x in data if x["id"] == t["id"] and x.get("commit")), None)
        if not sha: continue
        tmp = tempfile.mkdtemp(prefix="cb-ckpt-")
        try:
            if snapshot(code, sha, tmp):
                r = verify_workspace(ctx, tr, m, st, tmp); out.append({"after": t["id"], "passed": r["passed"], "total": r["total"], "pass_rate": r["pass_rate"]})
        finally: shutil.rmtree(tmp, ignore_errors=True)
    return out

def verify_workspace(ctx, tr, m, st, tree):
    if tr.get("stage"): r = R.verify(m, tree, cumulative_upto=tr["stage"] - 1)
    else: r = R.verify(m, tree)
    r.pop("tests", None); return r

def _trees_for_audit(ctx, tr, m, st, proj):
    """(starting workspace, trusted solution) trees for the plan audit."""
    seed, sol = tempfile.mkdtemp(prefix="cb-aud-seed-"), tempfile.mkdtemp(prefix="cb-aud-sol-")
    R.overlay(seed, *_seed_dirs(ctx, tr, m, st))
    if st: R.overlay_stages(sol, m, tr["stage"])
    else: R.build_solution(m, sol)
    return seed, sol

def verify_trial(ctx, tr, td, proj):
    m, st = _benchmark(ctx, tr); t0 = time.time(); code = W.code_dir(proj)
    td["reference"] = R.verify(m, code, cumulative_upto=tr["stage"] - 1) if tr.get("stage") else R.verify(m, code)
    td["reference"].pop("tests", None)
    store = T.TaskStore(proj); mt = MT.collect_tasks(proj) if store.exists() else {"tasks": [], "frozen": False}
    td["tasks"] = mt["tasks"]; td["totals"] = MT.project_totals(mt["tasks"], proj) if mt["frozen"] else {}
    w = (td.get("plan") or {}).get("window")
    if w: td["planning_model"] = MT.planning_stats(proj, w[0], w[1])
    from .. import roles as ROL
    td["roles"] = ROL.describe(proj)                    # exactly which model played which role, and whether it thought
    td["calls_sample"] = MT.model_calls(proj)[:2000]
    if ctx.state["mode"] != "minimal" and os.path.isdir(proj.p("checks")):
        seed, sol = _trees_for_audit(ctx, tr, m, st, proj)
        try: td["plan_audit"] = CL.plan_audit(proj, seed, sol)
        finally: shutil.rmtree(seed, ignore_errors=True); shutil.rmtree(sol, ignore_errors=True)
    specs = []
    for fn in sorted(os.listdir(proj.p("checks"))) if os.path.isdir(proj.p("checks")) else []:
        if fn.endswith(".spec.json") and not fn.startswith("S"): specs.append(RS.read_text(proj.p("checks", fn))[:4000])
    td["plan_specs"] = specs
    if ctx.cfg.get("checkpoints") and mt["frozen"]: td["checkpoints"] = checkpoint_curve(ctx, tr, m, st, proj, mt["tasks"])
    td["infra_tasks"] = []
    reqs = (st or m)["requirements"] if not st else [{**r, "id": f"{st['id']}:{r['id']}"} for s in m["stages"][:tr["stage"]] for r in s["requirements"]]
    td["classification"] = CL.summarize(td, reqs); td.pop("plan_specs", None)
    MT.save_evidence(proj, ctx.store.evidence_dir(tr["trial_id"]))
    td.setdefault("timing", {})["verify_s"] = round(time.time() - t0, 1)

def outcome_of(td):
    if td.get("outcome_hint"): return td["outcome_hint"]
    tasks = td.get("tasks") or []
    if not tasks: return "PLANNING_FAILED"
    v = sum(1 for t in tasks if t["status"] == "VERIFIED")
    return "COMPLETE" if v == len(tasks) else ("PARTIAL" if v else "FAILED")

# ---------- one trial ----------
def run_trial(ctx, tr):
    td = ctx.store.load_trial(tr["trial_id"]) or {"trial_id": tr["trial_id"], "benchmark": tr["benchmark"], "level": tr.get("level"), "stage": tr.get("stage"), "repeat": tr["repeat"], "mode": ctx.state["mode"]}
    tr["started"] = tr.get("started") or RS.now_iso(); td["started"] = tr["started"]
    m, st = _benchmark(ctx, tr); proj = None; ctx.log(f"== {tr['trial_id']} ({m['title']}{', stage ' + st['id'] if st else ''}) phase {tr['phase']}")
    try:
        if tr["phase"] in ("created", "planning"):
            _set_phase(ctx, tr, td, "planning"); proj = setup_trial(ctx, tr, td); _set_phase(ctx, tr, td, "planning")
            if not ensure_model_server(ctx): raise RunError("the model server is not reachable")
            info = plan_trial(ctx, tr, td, proj); ctx.log(f"planned: ok={info['ok']} tasks={info['tasks_planned']}/{info['spec_items']} items in {info['secs']} s")
            _set_phase(ctx, tr, td, "planned")
        proj = proj or Project(tr["project"], ctx.home)
        if tr["phase"] == "planned":
            if (td.get("plan") or {}).get("ok") and freeze_trial(ctx, tr, td, proj): _set_phase(ctx, tr, td, "frozen")
            else: td["outcome_hint"] = "PLANNING_FAILED"; ctx.log("no usable plan: the trial ends here (the empty workspace is still scored)"); _set_phase(ctx, tr, td, "loop_done")
        if tr["phase"] in ("frozen", "running"):
            _set_phase(ctx, tr, td, "running"); t0 = time.time(); prev = td.get("timing", {}).get("run_s", 0.0)
            res = (drive_engine if ctx.driver == "engine" else drive_loop)(ctx, tr, td, Project(tr["project"], ctx.home))
            td.setdefault("timing", {})["run_s"] = round(prev + time.time() - t0, 1)
            if res == "cancelled": ctx.log(f"{tr['trial_id']} cancelled (resumable)"); _set_phase(ctx, tr, td, "running"); return "cancelled"
            _set_phase(ctx, tr, td, "loop_done")
        if tr["phase"] == "loop_done":
            proj = Project(tr["project"], ctx.home); verify_trial(ctx, tr, td, proj)
            td["outcome"] = tr["outcome"] = outcome_of(td); tr["finished"] = td["finished"] = RS.now_iso(); _set_phase(ctx, tr, td, "finished")
            _history(ctx, tr, td); ctx.log(f"{tr['trial_id']}: {td['outcome']}, reference {td['reference']['passed']}/{td['reference']['total']}")
    except RunError as e:
        td["error"] = str(e); td["outcome"] = tr["outcome"] = "INFRASTRUCTURE_ERROR"; tr["finished"] = td["finished"] = RS.now_iso(); ctx.log(f"{tr['trial_id']} INFRASTRUCTURE_ERROR: {e}")
        ctx.notifier.critical(ctx.state["run_id"], f"{tr['trial_id']}: {e}")
        try: _salvage(ctx, tr, td)
        except Exception as e2: td["salvage_error"] = str(e2)[:300]       # a failed salvage must not hide the original error
        _set_phase(ctx, tr, td, "finished"); _history(ctx, tr, td)
    return "done"

def _salvage(ctx, tr, td):
    """After an infrastructure error keep whatever is measurable: metrics and evidence of the trial so far (reference score of the current tree too)."""
    if not tr.get("project"): return
    proj = Project(tr["project"], ctx.home); m, st = _benchmark(ctx, tr)
    try: td["reference"] = verify_workspace(ctx, tr, m, st, W.code_dir(proj))
    except Exception: pass
    mt = MT.collect_tasks(proj); td["tasks"] = mt["tasks"]; td["totals"] = MT.project_totals(mt["tasks"], proj) if mt["frozen"] else {}
    MT.save_evidence(proj, ctx.store.evidence_dir(tr["trial_id"]))

def _history(ctx, tr, td):
    env = ctx.state["environment"]; tt = td.get("totals") or {}; ref = td.get("reference") or {}
    RS.append_history(ctx.home, {"run_id": ctx.state["run_id"], "trial_id": tr["trial_id"], "benchmark": tr["benchmark"], "level": tr.get("level"), "stage": tr.get("stage"), "repeat": tr["repeat"], "mode": ctx.state["mode"],
        "model": ctx.cfg.get("model"), "commit": env.get("codebot_commit"), "dirty": env.get("codebot_dirty"), "framework": env.get("benchmark_framework_version"), "task_minutes": ctx.cfg["task_minutes"],
        "finished": td.get("finished"), "outcome": td.get("outcome"), "tasks_total": tt.get("tasks_total"), "verified": tt.get("verified"), "timeouts": tt.get("timeouts"), "internal_rate": tt.get("internal_success_rate"),
        "reference_rate": ref.get("pass_rate"), "run_s": (td.get("timing") or {}).get("run_s"), "plan_s": (td.get("plan") or {}).get("secs")})

# ---------- whole run ----------
def execute(ctx):
    """Run (or resume) every unfinished trial in order. Safe to call again after a crash or a cancel."""
    st = ctx.store.load(); ctx.store.clear_cancel()
    def f(s): s["status"] = "running"
    ctx.store.update(f); open(ctx.store.active_path(), "w").close()
    with open(ctx.store.pid_path(), "w") as fp: fp.write(str(os.getpid()))
    cancelled = False
    try:
        for tr in ctx.store.load()["trials"]:
            if tr["phase"] == "finished": continue
            if ctx.store.cancel_requested(): cancelled = True; ctx.log(f"cancel requested: stopping before {tr['trial_id']} (resumable)"); break      # a trial that ends at planning never reaches the loop's own check
            ctx.state = ctx.store.load(); cur = next(t for t in ctx.state["trials"] if t["trial_id"] == tr["trial_id"])
            if run_trial(ctx, cur) == "cancelled": cancelled = True; break
            from . import reports; reports.write_all(ctx.store)
        ctx.notifier.flush_timeouts(ctx.state["run_id"])
    finally:
        final = ctx.store.load()
        if cancelled:
            ctx.store.update(lambda s: s.update(status="cancelled"))
        elif all(t["phase"] == "finished" for t in final["trials"]):
            ctx.store.update(lambda s: s.update(status="finished", finished=RS.now_iso()))
            from . import reports; reports.write_all(ctx.store); ctx.notifier.complete(final["run_id"], reports.one_line(ctx.store))
        for p in (ctx.store.active_path(), ctx.store.pid_path()) if (cancelled or all(t["phase"] == "finished" for t in final["trials"])) else ():
            try: os.remove(p)
            except OSError: pass
        unload_model(ctx.home, ctx.cfg.get("model"), ctx.cfg.get("ollama_port")) if ctx.model_check else None
    return ctx.store.load()["status"]

def start_detached(home, run_id):
    """Run `execute` in its own session (survives the shell closing); progress in runner.log. Returns the pid."""
    store = RS.Store(home, run_id); os.makedirs(store.dir, exist_ok=True)
    with open(os.path.join(store.dir, "runner.log"), "a") as log:                                         # the child keeps its own copy of the handle
        p = subprocess.Popen([sys.executable, os.path.join(home, "bin", "codebot-bench"), "--home", home, "resume", run_id, "--foreground"], stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
    with open(store.pid_path(), "w") as f: f.write(str(p.pid))
    return p.pid
