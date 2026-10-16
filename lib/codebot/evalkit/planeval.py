"""Planner probe: N fresh plans per (planner model, think setting, level), each scored twice by planscore: the model's own assertions ("model",
what the planner alone is worth) and what codebot froze after its repairs, salvage and guard ("frozen", what the system ends up with).
Workspaces are made exactly as a benchmark trial makes them (capbench.workspace), so the planner sees the same thing it sees in a real run.
Results: bench/results/eval/<run>/plans.jsonl and summary.md."""
import glob, json, os, statistics, time
from .. import checkspec as K, planner as PL
from ..config import Project
from ..capbench import manifests as M, workspace as W
from ..capbench.runner import model_lock
from . import gate as G, planscore as PS

def _raw_tasks(proj, n_items):
    """The model's last reply for each item of round 1 (per-item planning), or the tasks of the round-1 whole-plan reply."""
    out = []
    for i in range(1, n_items + 1):
        f = sorted(glob.glob(proj.p(f"plan.reply.1.item{i}.*.json")))
        try: out.append(json.load(open(f[-1])) if f else None)
        except ValueError: out.append(None)
    if any(out): return out
    try:
        with open(proj.p("plan.reply.1.json")) as fh: return json.load(fh).get("tasks") or []
    except (OSError, ValueError): return []

def _planner_cost(proj):
    calls = gen = think = secs = 0
    try:
        with open(proj.p("audit.jsonl")) as f:
            for line in f:
                d = json.loads(line)
                if d.get("kind") == "model_request" and d.get("role") == "planner":
                    calls += 1; gen += int(d.get("gen_tokens", 0)); think += int(d.get("thinking_chars", 0)); secs += float(d.get("secs", 0))
    except (OSError, ValueError): pass
    return {"calls": calls, "gen_tokens": gen, "thinking_chars": think, "model_secs": round(secs)}

def one(home, run_id, level, i, model, think, plan_minutes=45, log=print, cfg_no=1):
    m = M.load(home, level); spec = M.spec_text(m)
    name = f"cap-eval-{run_id}-{m['_id'][-2:]}-c{cfg_no}-{i}"                   # c<n>: one workspace per configuration (they collided before)
    cfg = W.project_settings(home, 90, extra={"PLANNER_MODEL": model, "PLANNER_THINK": think})
    W.create(home, name, [M.seed_dir(m)], spec, cfg); proj = Project(name, home); t0 = time.time()
    with model_lock(home):
        info = PL.plan_auto(home, name, int(plan_minutes * 60), 3, log=lambda s: log(f"  [{name}] {s}"), max_tasks=m["planner"]["max_tasks"], num_predict=m["planner"].get("num_predict", 5000))
    secs = round(time.time() - t0)
    frozen = []
    for t in range(1, 21):
        a = K.read_spec(proj, f"T{t}")
        if a is None and not os.path.exists(proj.p("checks", f"T{t}.spec.json")): break
        frozen.append(a or [])
    raw = _raw_tasks(proj, max(PL.spec_items(spec), 1)); model_tasks, malformed = PS.model_view(raw)
    row = {"run": run_id, "level": m["_id"], "plan": i, "project": name, "model": model, "think": think, "ok": info["ok"], "secs": secs, "rounds": len(info["rounds"]),
           "dropped_tasks": info.get("dropped_tasks", []), "items": PL.spec_items(spec), "cost": _planner_cost(proj), "malformed_model_asserts": malformed,
           "frozen": PS.score(proj, m, frozen, spec) if frozen else None, "model_view": PS.score(proj, m, model_tasks, spec) if any(model_tasks) else None}
    return row

def _mean_sd(xs):
    xs = [x for x in xs if x is not None]
    if not xs: return "n/a"
    return f"{statistics.mean(xs):.2f}" + (f" ± {statistics.stdev(xs):.2f}" if len(xs) > 1 else "")

def summary(rows):
    L = ["| level | model | think | plans | ok | E | S | K | Q | asserts | minutes | gen tokens |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    groups = {}
    for r in rows: groups.setdefault((r["level"], r["model"], str(r["think"])), []).append(r)
    for (lv, mo, th), rs in sorted(groups.items()):
        f = [r["frozen"] or {} for r in rs]
        L.append(f"| {lv} | `{mo}` | {th} | {len(rs)} | {sum(r['ok'] for r in rs)} | {_mean_sd([x.get('E') for x in f])} | {_mean_sd([x.get('S') for x in f])} | "
                 f"{_mean_sd([x.get('K') for x in f])} | {_mean_sd([x.get('Q') for x in f])} | {_mean_sd([x.get('asserts') for x in f])} | "
                 f"{_mean_sd([r['secs'] / 60 for r in rs])} | {_mean_sd([r['cost']['gen_tokens'] for r in rs])} |")
    L += ["", "Model view (the planner's own assertions, before codebot's repairs and salvage):", "",
          "| level | model | think | E | S | K | Q | malformed |", "|---|---|---|---|---|---|---|---|"]
    for (lv, mo, th), rs in sorted(groups.items()):
        f = [r["model_view"] or {} for r in rs]
        L.append(f"| {lv} | `{mo}` | {th} | {_mean_sd([x.get('E') for x in f])} | {_mean_sd([x.get('S') for x in f])} | {_mean_sd([x.get('K') for x in f])} | "
                 f"{_mean_sd([x.get('Q') for x in f])} | {_mean_sd([r['malformed_model_asserts'] for r in rs])} |")
    return "\n".join(L) + "\n"

def run(home, models, thinks, levels, plans, name=None, plan_minutes=45, log=print):
    """Plans are interleaved (configuration-major inside each plan index), so heat and time-of-day drift spread over every configuration."""
    run_id = time.strftime("%Y%m%d-%H%M%S") + (f"-{name}" if name else "")
    out_dir = os.path.join(home, "bench", "results", "eval", run_id); os.makedirs(out_dir, exist_ok=True); rows = []
    configs = [(mo, th) for mo in models for th in thinks]
    for i in range(1, plans + 1):
        for lv in levels:
            for c, (mo, th) in enumerate(configs, 1):
                log(f"plan {i}/{plans} {lv} {mo} think={th}")
                try: r = one(home, run_id, lv, i, mo, th, plan_minutes, log, c)
                except (Exception, SystemExit) as e:                       # one broken plan must not end the probe r = {"run": run_id, "level": lv, "plan": i, "model": mo, "think": th, "ok": False, "error": f"{type(e).__name__}: {e}"[:400], "secs": 0, "cost": {}, "frozen": None, "model_view": None, "malformed_model_asserts": 0}
                rows.append(r)
                with open(os.path.join(out_dir, "plans.jsonl"), "a") as f: f.write(json.dumps(r) + "\n")
                fz = r.get("frozen") or {}
                log(f"  -> ok={r['ok']} E={fz.get('E')} S={fz.get('S')} K={fz.get('K')} Q={fz.get('Q')} in {r['secs']} s" + (f" ERROR {r['error']}" if r.get("error") else ""))
                with open(os.path.join(out_dir, "summary.md"), "w") as f: f.write(f"# Planner probe {run_id}\n\n" + summary(rows))
    made = next((r["project"] for r in rows if r.get("project")), None)
    if made:                                            # free RAM and heat: no model stays loaded after a probe
        for mo in models: G.unload(Project(made, home), mo)
    return run_id, out_dir, rows
