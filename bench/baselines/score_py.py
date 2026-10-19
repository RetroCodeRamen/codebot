"""Score bare_py.py runs with the level's hidden tests, through the bench's own runner (capbench.reference): round 1 (one-shot) and the
delivered round, each copied into a fresh seed. Writes score.json in each run. Usage: score_py.py LEVEL RUN_DIR [RUN_DIR ...]"""
import json, os, shutil, sys, tempfile

HOME = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the repo root
sys.path.insert(0, os.path.join(HOME, "lib"))
from codebot.capbench import reference as R

def score(cat, reqs, code):
    tmp = tempfile.mkdtemp(prefix="bare-score-"); ws = os.path.join(tmp, "ws")
    try:
        shutil.copytree(os.path.join(cat, "seed"), ws)
        for n in os.listdir(code): shutil.copy(os.path.join(code, n), ws)
        s = R.summarize(R.run_tests(os.path.join(cat, "reference_tests"), ws), reqs)
        return {"hidden": s["passed"], "hidden_total": s["total"], "failing": s["failing"],
                "by_req": {r: f"{v['passed']}/{v['total']}" for r, v in sorted(s["by_requirement"].items())}}
    finally: shutil.rmtree(tmp, ignore_errors=True)

def main():
    level, runs = sys.argv[1], sys.argv[2:]
    cat = os.path.join(HOME, "bench/ladder/catalog", level); reqs = json.load(open(os.path.join(cat, "manifest.json")))["requirements"]
    line = lambda v: "no code" if not v else f"{v['hidden']}/{v['hidden_total']} hidden  " + " ".join(f"{r}:{x}" for r, x in v["by_req"].items())
    for run in runs:
        sm = json.load(open(os.path.join(run, "summary.json"))); out = {"summary": sm}
        for tag, rnd in (("one_shot", 1), ("delivered", sm.get("delivered_round"))):
            d = os.path.join(run, f"round-{rnd:02d}", "code") if rnd else None
            out[tag] = score(cat, reqs, d) if d and os.path.isdir(d) else None
        with open(os.path.join(run, "score.json"), "w") as f: json.dump(out, f, indent=1)
        print(f"{os.path.basename(run.rstrip('/'))}: rounds={sm['rounds']} drafts={sm['drafts']} stop={sm['stop']} minutes={sm['minutes']} "
              f"delivered=round {sm.get('delivered_round')} ({sm.get('delivered_examples')}/{sm['examples_total']} examples)\n"
              f"  one-shot : {line(out['one_shot'])}\n  delivered: {line(out['delivered'])}", flush=True)

if __name__ == "__main__": main()
