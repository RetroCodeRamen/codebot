"""Score a plan's assertions against a level's reference solution, its bare seed and its mutants (mutants.py). All model-written assertions run
inside the codebot sandbox (Docker), never on the host: a candidate model's `run` assertions have not been reviewed by anyone.

Metrics (docs/MODEL_EVAL.md):
  E  executability   share of tasks with no impossible assertion (one is enough to doom a task: it can never pass)
  S  soundness       share of assertions that hold on the reference solution
  K  coverage        per requirement, the share of its mutants that the plan's SOUND assertions reject; K is the mean over requirements
                     (unsound assertions are left out: they would "catch" every mutant, correct code included)
  Q  quality         E x K
plus vacuous assertions (hold on the seed), duplicates, invented exceptions (a `raises` naming a class the spec never mentions), tasks without a
behaviour test, functions per task, and the unsound assertions grouped by kind."""
import json, os, re, shutil, tempfile
from .. import assertions as A, checkspec as K, sandbox
from ..capbench import manifests as M, reference as R
from . import mutants as MU

FOCUSED_MAX = 2          # a mutant that breaks more requirements than this hits shared core code: almost any assertion catches it, so it says
                         # little about coverage; such "broad" mutants are reported as K_broad and left out of K (level 2: 87 of 169 are broad)

DRIVER = r'''
import json, os, signal, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import assert_runner as A
job = json.load(open(sys.argv[1])); out = {}
def alarm(*_): raise TimeoutError("assertion took longer than 5 s")
signal.signal(signal.SIGALRM, alarm)
for name, ws in job["workspaces"].items():
    pid_r, pid_w = os.pipe(); pid = os.fork()
    if pid == 0:                                   # a fresh process per workspace: modules are imported from that workspace only
        os.close(pid_r); os.chdir(ws); sys.path.insert(0, ws); res = []
        for a in job["asserts"]:
            signal.alarm(5)
            try: m = A.check([a]); res.append(m[0] if m else None)
            except BaseException as e: res.append(f"{type(e).__name__}: {str(e)[:150]}")
            finally: signal.alarm(0)
        os.write(pid_w, json.dumps(res).encode()); os._exit(0)
    os.close(pid_w); buf = b""
    while True:
        chunk = os.read(pid_r, 65536)
        if not chunk: break
        buf += chunk
    os.waitpid(pid, 0); os.close(pid_r)
    try: out[name] = json.loads(buf.decode())
    except ValueError: out[name] = ["the evaluation process died"] * len(job["asserts"])
print("@@RESULT@@" + json.dumps(out))
'''

def kind(msg):
    m = msg or ""
    if re.search(r"positional argument|required positional|unexpected keyword", m): return "signature"
    if "cannot import" in m or "does not exist" in m: return "names something the spec does not define"
    if "instead of raising" in m: return "expects an exception the code does not raise"
    if re.search(r"raised \w+.*expected", m): return "expects a value where the code raises"
    if "returned" in m or "printed" in m: return "wrong expected value"
    if "exited" in m: return "wrong exit status"
    if "contain" in m or "file " in m: return "source text / file"
    return "other"

def judge(proj, m, asserts, with_mutants=True, timeout=1800):
    """{"reference": [msg|None per assertion], "seed": [...], "<mutant id>": [...]} evaluated in the sandbox."""
    muts = (MU.load(m) or {}).get("mutants", []) if with_mutants else []
    tmp = tempfile.mkdtemp(prefix="cb-score-", dir=os.path.join(proj.home, "bench", "runs"))
    try:
        ref = os.path.join(tmp, "reference"); R.build_solution(m, ref)
        seed = os.path.join(tmp, "seed"); R.overlay(seed, M.seed_dir(m))
        ws = {"reference": ref, "seed": seed}
        for i, mu in enumerate(muts):
            d = os.path.join(tmp, f"mut{i}"); shutil.copytree(ref, d)
            with open(os.path.join(d, mu["file"]), "w") as f: f.write(mu["source"])
            ws[mu["id"]] = d
        shutil.copyfile(A.__file__, os.path.join(tmp, "assert_runner.py"))
        with open(os.path.join(tmp, "driver.py"), "w") as f: f.write(DRIVER)
        with open(os.path.join(tmp, "job.json"), "w") as f: json.dump({"asserts": asserts, "workspaces": ws}, f)
        rc, out, label = sandbox.execute(proj, ["python3", os.path.join(tmp, "driver.py"), os.path.join(tmp, "job.json")], timeout=timeout, rw=[tmp], ro=[], cwd=tmp)
        if "@@RESULT@@" not in out: raise RuntimeError(f"scoring failed in the sandbox ({label}, rc {rc}): {out[-400:]}")
        res = json.loads(out.split("@@RESULT@@", 1)[1].strip().splitlines()[0]); res["_sandbox"] = label; return res
    finally: shutil.rmtree(tmp, ignore_errors=True)

def score(proj, m, tasks, spec_text, with_mutants=True):
    """tasks: [[assertion, ...] per task] (an empty or None list = a task with no usable assertion). Returns the metrics dict."""
    flat, owner = [], []
    for i, a in enumerate(tasks):
        for x in a or []: flat.append(x); owner.append(i)
    res = judge(proj, m, flat, with_mutants) if flat else {"reference": [], "seed": [], "_sandbox": "none"}
    ref, seed = res["reference"], res["seed"]
    sound = [msg is None for msg in ref]
    per_task = []
    for i, a in enumerate(tasks):
        idx = [k for k, o in enumerate(owner) if o == i]
        funcs = sorted({flat[k].get("func") for k in idx if flat[k].get("type") == "call"})
        per_task.append({"task": f"T{i + 1}", "n": len(idx), "unsound": [ref[k] for k in idx if ref[k]], "vacuous": sum(1 for k in idx if seed[k] is None),
                         "behaviour_tests": sum(1 for k in idx if flat[k].get("type") in ("call", "run")), "funcs": funcs})
    muts = (MU.load(m) or {}).get("mutants", []) if with_mutants else []
    reqs = [r["id"] for r in M.requirements(m)]; per_req = {}; broad = [0, 0]
    for mu in muts:
        killed = any(sound[k] and res[mu["id"]][k] is not None for k in range(len(flat)))
        if len(mu["requirements"]) > FOCUSED_MAX: broad[1] += 1; broad[0] += killed; continue
        for r in mu["requirements"]:
            d = per_req.setdefault(r, [0, 0]); d[1] += 1; d[0] += killed
    k_req = {r: (per_req[r][0] / per_req[r][1]) if r in per_req else None for r in reqs}
    known = [v for v in k_req.values() if v is not None]
    seen, dups = set(), 0
    for x in flat:
        key = json.dumps(x, sort_keys=True); dups += key in seen; seen.add(key)
    invented = [x for x in flat if x.get("raises") and x["raises"] not in spec_text]
    E = sum(1 for t in per_task if t["n"] and not t["unsound"]) / len(per_task) if per_task else 0.0
    Kc = sum(known) / len(known) if known else None
    kinds = {}
    for msg in ref:
        if msg: kinds[kind(msg)] = kinds.get(kind(msg), 0) + 1
    return {"tasks": len(per_task), "asserts": len(flat), "E": round(E, 3), "S": round(sum(sound) / len(flat), 3) if flat else None,
            "K": round(Kc, 3) if Kc is not None else None, "K_by_requirement": {r: (round(v, 3) if v is not None else None) for r, v in k_req.items()},
            "requirements_covered": sum(1 for v in known if v > 0), "requirements_total": len(reqs), "Q": round(E * Kc, 3) if Kc is not None else None,
            "mutants": len(muts), "K_broad": round(broad[0] / broad[1], 3) if broad[1] else None, "vacuous": sum(t["vacuous"] for t in per_task), "duplicates": dups, "invented_exceptions": len(invented),
            "tasks_without_behaviour_test": sum(1 for t in per_task if not t["behaviour_tests"]), "unsound_by_kind": kinds, "per_task": per_task, "sandbox": res.get("_sandbox")}

def model_view(raw_tasks):
    """The model's own assertions (before codebot's repairs and salvage), each reduced to what can be executed at all: an assertion the runner
    cannot even read is counted by the caller as malformed, not run."""
    out, malformed = [], 0
    for t in raw_tasks:
        keep = []
        for a in (t or {}).get("asserts") or []:
            if A.validate([a]) is None: keep.append(a)
            else: malformed += 1
        out.append(keep)
    return out, malformed
