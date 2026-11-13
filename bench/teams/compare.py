#!/usr/bin/env python3
"""Drafts mode against orchestrated builds on the same specs, seeds and wall-clock budget, scored by a catalog entry's hidden tests.

  python3 bench/teams/compare.py --name NAME [--tasks toolbox_04,toolbox_10] [--arms drafts,plan,review] [--seeds 1,2,3] [--minutes 30]

Arms (one trial = one task, one arm, one seed; a trial is never a stage of another trial):
  drafts   drafts mode alone (`codebot new` + `codebot start`, in process)
  plan     planner, then the drafts loop                         PIPELINE_SHAPE=plan
  review   planner, drafts loop, fresh reviewer, fix             PIPELINE_SHAPE=small
  full     manager, one team per part, integration, QA           PIPELINE_SHAPE=large
  auto     whatever the Team Designer's size estimate chooses    (no PIPELINE_SHAPE)

Every arm gets the same --minutes of wall clock for everything it does. Drafts: DRAFTS_MINUTES. An orchestrated arm: the design call is timed
and the rest is the run's own budget (PIPELINE_MINUTES, enforced by the scheduler: status EXHAUSTED); inside it the build step's drafts loop gets
BUILD_SHARE of the budget, so the steps after it have time. The model, the context size and the seed (MODEL_SEED and DRAFTS_SEED) are the same
for every arm. Each trial has its own project (projects/cmp-NAME-...) and code dir (bench/runs/.../code, its own git repository), as the ladder
benchmark makes them (capbench.workspace): nothing is shared with a production project. Trials run one at a time under the model lock, seed by
seed with the arms next to each other, so a series stopped early is still balanced. Results are appended to bench/teams/results/NAME.jsonl and
a rerun of the same NAME continues after them.
"""
import argparse, json, os, sys, time
HOME = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(HOME, "lib"))
from codebot import drafts as DR, thermal                                       # noqa: E402
from codebot.config import Project                                               # noqa: E402
from codebot.capbench import manifests as M, reference as R, workspace as W, runner as RN     # noqa: E402
from codebot.pipeline import design, frozen, schedule, runtime as PR            # noqa: E402

SHAPE = {"plan": "plan", "review": "small", "full": "large", "auto": None}
BUILD_SHARE = {"plan": 0.9, "review": 0.65, "full": 0.0, "auto": 0.65}

def log(*a): print(time.strftime("%H:%M:%S"), *a, flush=True)

def calls(proj):
    """Model calls of a trial from its audit log: count, seconds, tokens."""
    n = secs = pt = gt = 0
    try:
        with open(proj.p("audit.jsonl")) as f:
            for ln in f:
                try: r = json.loads(ln)
                except ValueError: continue
                if r.get("kind") == "model_request": n += 1; secs += r.get("secs") or 0; pt += r.get("prompt_tokens") or 0; gt += r.get("gen_tokens") or 0
    except OSError: pass
    return {"calls": n, "model_s": round(secs, 1), "prompt_tokens": pt, "gen_tokens": gt}

def run_drafts(proj, cap_s):
    while True:
        with RN.model_lock(HOME): ev = DR.step(proj)
        if ev["event"] in ("all_done", "finished_incomplete", "error"): break
        if ev["event"] == "model_error": time.sleep(30)
    s = DR.summary(proj) if ev["event"] != "error" else {}
    return {"end": ev["event"], "error": ev.get("error"), "rounds": s.get("rounds"), "examples": s.get("delivered_examples"), "examples_total": s.get("examples_total")}

def run_team(proj, spec, minutes, t0):
    open(proj.p(".git-prepped"), "w").close()                  # the trial's code dir is its own repository under bench/runs: checkpoints may commit there
    with RN.model_lock(HOME): bundle, meta = design.propose(HOME, spec, proj=proj)
    design.write_project_pipeline(proj, bundle, template_meta=meta)
    left = max(0.5, minutes - (time.time() - t0) / 60)         # the design call came out of the same budget
    with open(proj.p("project.env"), "a") as f: f.write(f'PIPELINE_MINUTES="{left:.2f}"\n')
    proj = Project(proj.name, HOME); members = [m["id"] for m in bundle["team"]["members"]]
    log("   team:", meta.get("shape"), members, "|", (meta.get("fit") or {}).get("why", "")[:110])
    while True:
        if time.time() - t0 > minutes * 60 * 1.25 + 120: schedule.cancel(proj); break          # a backstop only: the scheduler's own budget ends the run
        with RN.model_lock(HOME): ev = schedule.step(proj)
        if ev.get("status") in schedule.TERMINAL or ev.get("status") == "WAITING_APPROVAL": break
        if ev.get("status") == "BUSY": time.sleep(10)
    run = PR.load_run(proj) or {}; gates = run.get("gates") or {}; outs = [o["outcome"] for o in run.get("outcomes") or []]
    return {"end": run.get("status"), "result": run.get("result"), "error": run.get("error"), "shape": meta.get("shape"), "members": members, "steps": run.get("step_count"),
            "fit": meta.get("fit"), "fit_actual": run.get("fit_actual"), "usage": run.get("usage"), "used_s": run.get("used_s"),
            "gates": {k: (g.get("result"), g.get("fails"), (g.get("detail") or "").splitlines()[0][:120]) for k, g in gates.items()},
            "gate_retries": sum(int(g.get("fails") or 0) for g in gates.values()), "regressions": sum((run.get("regressions") or {}).values()),
            "rollbacks": len(run.get("rollbacks") or []), "exhausted_agents": sum(1 for o in outs if o.startswith("exhausted")), "failed_agents": outs.count("failed"),
            "human_interventions": sum(1 for a in (run.get("approvals") or {}).values() if not str(a.get("by", "")).startswith("auto")),
            "recovered_actions": run.get("recovered_actions", 0), "skipped": run.get("skipped") or [], "warnings": run.get("warnings") or [],
            "progress": [(p["node"], p["passed"], p["total"]) for p in run.get("progress") or []], "frozen": bool(frozen.info(proj))}

def trial(name, task, arm, seed, minutes):
    m = M.load(HOME, task); spec = M.spec_text(m); pname = f"cmp-{name}-{task}-{arm}-s{seed}".lower().replace("_", "-")
    W.destroy(HOME, pname)
    build = minutes if arm == "drafts" else max(1.0, minutes * BUILD_SHARE[arm])
    extra = {"DRAFTS_MINUTES": f"{build:g}", "DRAFTS_SEED": str(seed), "WORKFLOW": "drafts" if arm == "drafts" else None, "PIPELINE_SHAPE": SHAPE.get(arm)}
    proj = W.create(HOME, pname, [M.seed_dir(m)], spec, W.project_settings(HOME, minutes, None, seed, extra=extra))
    t0 = time.time(); temp0 = thermal.read_temp()
    try: info = run_drafts(proj, minutes * 60) if arm == "drafts" else run_team(proj, spec, minutes, t0)
    except Exception as e: info = {"end": "ERROR", "error": f"{type(e).__name__}: {e}"}
    wall = round(time.time() - t0, 1); proj = Project(pname, HOME)
    ref = R.verify(m, W.code_dir(proj)); ref.pop("tests", None)
    return {"task": task, "arm": arm, "seed": seed, "project": pname, "minutes_budget": minutes, "wall_s": wall, "hidden": ref.get("passed"), "hidden_total": ref.get("total"),
            "by_requirement": {k: (v.get("passed"), v.get("total")) for k, v in (ref.get("by_requirement") or {}).items()}, **calls(proj), **info,
            "temp_start": temp0, "temp_end": thermal.read_temp(), "finished": time.strftime("%Y-%m-%dT%H:%M:%S")}

def table(rows):
    out = []
    for task in sorted({r["task"] for r in rows}):
        for arm in sorted({r["arm"] for r in rows}):
            rs = sorted((r for r in rows if r["task"] == task and r["arm"] == arm), key=lambda r: r["seed"])
            if rs: out.append(f"{task:10} {arm:7} hidden " + ", ".join(f"{r['hidden']}/{r['hidden_total']}" for r in rs) + " | ended " + ", ".join(str(r.get("result") or r["end"]) for r in rs) + " | calls " + ", ".join(str(r["calls"]) for r in rs)
                              + " | minutes " + ", ".join(f"{r['wall_s'] / 60:.1f}" for r in rs))
    return "\n".join(out)

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--name", required=True); ap.add_argument("--tasks", default="toolbox_04,toolbox_10"); ap.add_argument("--arms", default="drafts,plan,review")
    ap.add_argument("--seeds", default="1,2,3"); ap.add_argument("--minutes", type=float, default=30.0, help="wall-clock budget of every trial, whatever its arm"); a = ap.parse_args()
    bad = [x for x in a.arms.split(",") if x != "drafts" and x not in SHAPE]
    if bad: raise SystemExit(f"unknown arm(s) {bad}: drafts, {', '.join(SHAPE)}")
    out = os.path.join(HOME, "bench", "teams", "results", a.name + ".jsonl"); os.makedirs(os.path.dirname(out), exist_ok=True)
    done = []
    if os.path.isfile(out):
        with open(out) as f: done = [json.loads(ln) for ln in f if ln.strip()]
    have = {(r["task"], r["arm"], r["seed"]) for r in done}
    plan = [(t, arm, int(s)) for s in a.seeds.split(",") for t in a.tasks.split(",") for arm in a.arms.split(",")]
    log(f"{len(plan)} trials, {len(have)} already done, {a.minutes:g} min of wall clock each -> {out}")
    if not RN.model_up(HOME): raise SystemExit("the model server is not reachable (codebot setup)")
    try:
        for task, arm, seed in plan:
            if (task, arm, seed) in have: continue
            log(f"== {task} {arm} seed {seed}")
            r = trial(a.name, task, arm, seed, a.minutes); done.append(r)
            with open(out, "a") as f: f.write(json.dumps(r) + "\n")
            log(f"   {r['end']}" + (f" ({r['result']})" if r.get("result") else "") + f": hidden {r['hidden']}/{r['hidden_total']}, {r['calls']} calls, {r['wall_s'] / 60:.1f} min, {r.get('temp_end')} C" + (f" | {r['error']}" if r.get("error") else ""))
    finally:
        RN.unload_model(HOME)
    print(table(done)); log("done")

if __name__ == "__main__": main()
