"""Score bare-baseline runs: the 32 hidden tests through the bench's own runner (capbench.reference) and the 20 hand inputs (handtest.py).
Scores round 1 (one-shot) and the delivered round of each run. Usage: score.py RUN_DIR [RUN_DIR ...]"""
import json, os, re, shutil, subprocess, sys, tempfile

HOME = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the repo root
sys.path.insert(0, os.path.join(HOME, "lib"))
from codebot.capbench import reference as R

CAT = os.path.join(HOME, "bench/ladder/catalog/calc_go")
REQS = json.load(open(os.path.join(CAT, "manifest.json")))["requirements"]
HAND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "handtest.py")

def score(main_go):
    tmp = tempfile.mkdtemp(prefix="bare-score-"); ws = os.path.join(tmp, "ws")
    try:
        shutil.copytree(os.path.join(CAT, "seed"), ws); shutil.copy(main_go, os.path.join(ws, "main.go"))
        s = R.summarize(R.run_tests(os.path.join(CAT, "reference_tests"), ws), REQS)
        h = subprocess.run([sys.executable, HAND, ws], capture_output=True, text=True, timeout=900).stdout
        m = re.search(r"(\d+)/(\d+) hand inputs", h)
        return {"hidden": s["passed"], "hidden_total": s["total"], "hand": int(m.group(1)) if m else 0, "hand_total": 20,
                "by_req": {r: f"{v['passed']}/{v['total']}" for r, v in sorted(s["by_requirement"].items())}, "failing": s["failing"], "hand_out": h}
    finally: shutil.rmtree(tmp, ignore_errors=True)

for run in sys.argv[1:]:
    sm = json.load(open(os.path.join(run, "summary.json"))); out = {"summary": sm}
    for tag, rnd in (("one_shot", 1), ("delivered", sm.get("delivered_round"))):
        f = os.path.join(run, f"round-{rnd:02d}", "main.go") if rnd else None
        out[tag] = score(f) if f and os.path.exists(f) else None
    with open(os.path.join(run, "score.json"), "w") as f: json.dump(out, f, indent=1)
    line = lambda v: "no code" if not v else f"{v['hidden']}/{v['hidden_total']} hidden, {v['hand']}/20 hand  " + " ".join(f"{r}:{x}" for r, x in v["by_req"].items())
    print(f"{os.path.basename(run.rstrip('/'))}: rounds={sm['rounds']} stop={sm['stop']} minutes={sm['minutes']} delivered=round {sm.get('delivered_round')} "
          f"({sm.get('delivered_examples')}/{sm['examples_total']} examples)\n  one-shot : {line(out['one_shot'])}\n  delivered: {line(out['delivered'])}", flush=True)
