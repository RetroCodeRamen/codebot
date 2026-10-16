"""`codebot eval mutants | gate | planner` (docs/MODEL_EVAL.md)."""
import argparse, json, os, sys, time
from ..config import Project
from ..capbench import manifests as M, workspace as W
from . import gate as G, mutants as MU, planeval as PE

def _levels(home, text): return [f"level_{n:02d}" for n in M.parse_levels(home, text)]

def _scratch(home):
    """A throwaway project that only supplies server settings (port, num_ctx) to the gate."""
    name = "cap-eval-gate-" + time.strftime("%Y%m%d-%H%M%S")
    m = M.load(home, "level_01"); W.create(home, name, [M.seed_dir(m)], M.spec_text(m), W.project_settings(home, 90)); return Project(name, home)

def main(argv, home):
    ap = argparse.ArgumentParser(prog="codebot eval"); sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("mutants", help="build the mutant sets of the reference solutions (no model)"); a.add_argument("--levels", default="1-3")
    g = sub.add_parser("gate", help="compatibility and speed gate per model"); g.add_argument("models", nargs="+"); g.add_argument("--think", default="off"); g.add_argument("-n", type=int, default=3)
    p = sub.add_parser("planner", help="N scored plans per planner model / think setting / level")
    p.add_argument("--models", required=True, help="comma-separated planner models"); p.add_argument("--think", default="off", help="comma-separated: off,on")
    p.add_argument("--levels", default="1"); p.add_argument("--plans", type=int, default=5); p.add_argument("--name"); p.add_argument("--plan-minutes", type=float, default=45)
    a = ap.parse_args(argv)
    if a.cmd == "mutants":
        for lv in _levels(home, a.levels): MU.build(M.load(home, lv))
        return 0
    if a.cmd == "gate":
        proj = _scratch(home); think = {"off": False, "on": True}.get(a.think, a.think); rows = []
        for mo in a.models: rows.append(G.run(proj, mo, think, a.n))
        out = os.path.join(home, "bench", "results", "eval"); os.makedirs(out, exist_ok=True)
        path = os.path.join(out, f"gate-{time.strftime('%Y%m%d-%H%M%S')}.json")
        with open(path, "w") as f: json.dump(rows, f, indent=1)
        print(f"results: {path}"); return 0 if all(r["ok"] for r in rows) else 1
    if a.cmd == "planner":
        for lv in _levels(home, a.levels):
            if not MU.load(M.load(home, lv)): print(f"{lv}: no current mutant set; run `codebot eval mutants --levels ...` first"); return 2
        run_id, out_dir, rows = PE.run(home, a.models.split(","), a.think.split(","), _levels(home, a.levels), a.plans, a.name, a.plan_minutes)
        print(f"run {run_id}: {len(rows)} plans; summary: {os.path.join(out_dir, 'summary.md')}"); return 0
    return 2
