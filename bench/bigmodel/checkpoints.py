#!/usr/bin/env python3
"""Progress of a finished drafts-mode trial at fixed minutes, without having interrupted it: the version drafts mode had delivered by then
(the best so far is committed every time it improves; rounds.jsonl gives each round's cumulative used time), scored by the bench's hidden tests.

  checkpoints.py RUN_ID [--minutes 45,90,150,180]
Prints one line per trial and minute: the delivered round, its spec examples, and the hidden tests passed.
"""
import glob, json, os, shutil, subprocess, sys, tempfile

H = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(H, "lib"))
from codebot.capbench import manifests as M, reference as R  # noqa: E402


def delivered_at(proj_dir, minutes):
    """(round, examples, commit) of the best version delivered by `minutes` of used time, or None."""
    rounds = [json.loads(l) for l in open(os.path.join(proj_dir, "drafts", "rounds.jsonl"))]
    best = None
    audit = [json.loads(l) for l in open(os.path.join(proj_dir, "audit.jsonl")) if '"drafts_deliver"' in l]
    commit_of = {a["round"]: a.get("commit") for a in audit}
    for r in rounds:
        if r["used_s"] > minutes * 60: break
        if r["round"] in commit_of: best = (r["round"], r.get("examples"), commit_of[r["round"]], round(r["used_s"] / 60, 1))
    return best


def hidden(bench, code_dir, commit):
    tmp = tempfile.mkdtemp(prefix="cb-ckpt-")
    try:
        a = subprocess.run(["git", "-C", code_dir, "archive", commit], capture_output=True, check=True)
        subprocess.run(["tar", "-x", "-C", tmp], input=a.stdout, check=True)
        r = R.verify(M.load(H, bench), tmp); return r["passed"], r["total"]
    finally: shutil.rmtree(tmp, ignore_errors=True)


def main():
    a = sys.argv[1:]; run = a[0]
    mins = [float(x) for x in (a[a.index("--minutes") + 1] if "--minutes" in a else "45,90,150,180").split(",")]
    for tj in sorted(glob.glob(f"{H}/bench/results/ladder/{run}/trials/*/trial.json")):
        d = json.load(open(tj)); tid = d["trial_id"]; bench = d.get("benchmark") or f"level_{int(d['level']):02d}"
        proj = f"cap-{run}-{tid.lower().replace('_', '-')}"
        pd, code = os.path.join(H, "projects", proj), os.path.join(H, "bench", "runs", proj, "code")
        dr = d.get("drafts") or {}
        print(f"{tid}: finished by {dr.get('stop')} after {dr.get('minutes')} min, final hidden {d.get('reference', {}).get('passed')}/{d.get('reference', {}).get('total')}")
        for m in mins:
            b = delivered_at(pd, m)
            if not b: print(f"  {m:>5g} min: nothing delivered yet"); continue
            p, t = hidden(bench, code, b[2])
            print(f"  {m:>5g} min: round {b[0]} (at {b[3]} min), {b[1]}/{dr.get('examples_total')} examples, hidden {p}/{t}")


if __name__ == "__main__":
    main()
