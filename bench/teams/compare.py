#!/usr/bin/env python3
"""Drafts mode against a designed team on the same specs, seeds and time cap, scored by the catalog's hidden tests.

  python3 bench/teams/compare.py --name NAME [--tasks team_a,team_b] [--arms drafts,team] [--seeds 1,2,3] [--minutes 20]

One trial = one task, one arm, one seed, in its own project (projects/cmp-NAME-...) and code dir (bench/runs/.../code, its own git repo), as
the ladder benchmark makes them (capbench.workspace). The drafts arm is `codebot new` + `codebot start` in process: drafts.step until it
finishes. The team arm is `codebot pipeline design` (the model proposes who writes which files) and then schedule.step until the run ends or
the time cap, which counts the design call too. Both get the same MODEL_SEED / DRAFTS_SEED and the same cap. Trials run one at a time under
the model lock, seed by seed with the arms next to each other, so a series that is stopped early is still balanced. A finished trial is never
rerun: results are appended to bench/teams/results/NAME.jsonl and a rerun of the same NAME continues after them.
"""
import argparse, json, os, sys, time
HOME = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(HOME, "lib"))
from codebot import drafts as DR, thermal                                       # noqa: E402
from codebot.config import Project                                               # noqa: E402
from codebot.capbench import manifests as M, reference as R, workspace as W, runner as RN     # noqa: E402
from codebot.pipeline import design, frozen, schedule, runtime as PR            # noqa: E402

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

def run_team(proj, spec, cap_s, t0):
    open(proj.p(".git-prepped"), "w").close()                  # the trial's code dir is its own repository: checkpoints may commit there
    with RN.model_lock(HOME): bundle, meta = design.propose(HOME, spec, proj=proj)
    design.write_project_pipeline(proj, bundle, template_meta=meta)
    proj = Project(proj.name, HOME); members = [m["id"] for m in bundle["team"]["members"]]
    log("   team:", meta.get("template"), members, "|", (meta.get("summary") or "")[:100])
    while True:
        if time.time() - t0 > cap_s: schedule.cancel(proj); break
        with RN.model_lock(HOME): ev = schedule.step(proj)
        if ev.get("status") in ("COMPLETED", "FAILED", "CANCELLED"): break
        if ev.get("status") == "BUSY": time.sleep(10)
    run = PR.load_run(proj) or {}
    gate = next(iter((run.get("gates") or {}).values()), {})
    return {"end": run.get("status"), "error": run.get("error"), "template": meta.get("template"), "members": members, "steps": run.get("step_count"),
            "gate": (gate.get("result"), (gate.get("detail") or "").splitlines()[0][:120] if gate else None), "gate_fails": gate.get("fails"),
            "warnings": run.get("warnings") or [], "regressions": run.get("regressions") or {}, "progress": [(p["node"], p["passed"], p["total"]) for p in run.get("progress") or []],
            "frozen": bool(frozen.info(proj))}

def trial(name, task, arm, seed, minutes):
    m = M.load(HOME, task); spec = M.spec_text(m); pname = f"cmp-{name}-{task}-{arm}-s{seed}".lower().replace("_", "-")
    W.destroy(HOME, pname)
    extra = {"DRAFTS_MINUTES": f"{minutes:g}", "DRAFTS_SEED": str(seed), "WORKFLOW": "drafts" if arm == "drafts" else None}
    proj = W.create(HOME, pname, [M.seed_dir(m)], spec, W.project_settings(HOME, minutes, None, seed, extra=extra))
    t0 = time.time(); temp0 = thermal.read_temp()
    try: info = run_drafts(proj, minutes * 60) if arm == "drafts" else run_team(proj, spec, minutes * 60, t0)
    except Exception as e: info = {"end": "ERROR", "error": f"{type(e).__name__}: {e}"}
    wall = round(time.time() - t0, 1); proj = Project(pname, HOME)
    ref = R.verify(m, W.code_dir(proj)); ref.pop("tests", None)
    return {"task": task, "arm": arm, "seed": seed, "project": pname, "minutes_cap": minutes, "wall_s": wall, "hidden": ref.get("passed"), "hidden_total": ref.get("total"),
            "by_requirement": {k: (v.get("passed"), v.get("total")) for k, v in (ref.get("by_requirement") or {}).items()}, **calls(proj), **info,
            "temp_start": temp0, "temp_end": thermal.read_temp(), "finished": time.strftime("%Y-%m-%dT%H:%M:%S")}

def table(rows):
    out = []
    for task in sorted({r["task"] for r in rows}):
        for arm in sorted({r["arm"] for r in rows}):
            rs = sorted((r for r in rows if r["task"] == task and r["arm"] == arm), key=lambda r: r["seed"])
            if rs: out.append(f"{task:8} {arm:7} hidden " + ", ".join(f"{r['hidden']}/{r['hidden_total']}" for r in rs) + " | calls " + ", ".join(str(r["calls"]) for r in rs)
                              + " | minutes " + ", ".join(f"{r['wall_s'] / 60:.1f}" for r in rs))
    return "\n".join(out)

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--name", required=True); ap.add_argument("--tasks", default="team_a,team_b"); ap.add_argument("--arms", default="drafts,team")
    ap.add_argument("--seeds", default="1,2,3"); ap.add_argument("--minutes", type=float, default=20.0); a = ap.parse_args()
    out = os.path.join(HOME, "bench", "teams", "results", a.name + ".jsonl"); os.makedirs(os.path.dirname(out), exist_ok=True)
    done = []
    if os.path.isfile(out):
        with open(out) as f: done = [json.loads(ln) for ln in f if ln.strip()]
    have = {(r["task"], r["arm"], r["seed"]) for r in done}
    plan = [(t, arm, int(s)) for s in a.seeds.split(",") for t in a.tasks.split(",") for arm in a.arms.split(",")]
    log(f"{len(plan)} trials, {len(have)} already done, cap {a.minutes:g} min each -> {out}")
    if not RN.model_up(HOME): raise SystemExit("the model server is not reachable (codebot setup)")
    try:
        for task, arm, seed in plan:
            if (task, arm, seed) in have: continue
            log(f"== {task} {arm} seed {seed}")
            r = trial(a.name, task, arm, seed, a.minutes); done.append(r)
            with open(out, "a") as f: f.write(json.dumps(r) + "\n")
            log(f"   {r['end']}: hidden {r['hidden']}/{r['hidden_total']}, {r['calls']} calls, {r['wall_s'] / 60:.1f} min, {r.get('temp_end')} C" + (f" | {r['error']}" if r.get("error") else ""))
    finally:
        RN.unload_model(HOME)
    print(table(done)); log("done")

if __name__ == "__main__": main()
