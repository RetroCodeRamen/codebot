"""Independent reference verification. The reference tests live in the catalog (outside every workspace and outside the sandbox mounts), are never
shown to the worker, and run against a COPY of the workspace so they cannot change what is being judged. Test names carry the requirement:
`test_r03_...` belongs to requirement R3 of the spec. A test that cannot run counts as a failure, never as a pass; skips are reported and flagged."""
import json, os, re, shutil, subprocess, sys, tempfile, time
from . import manifests as M

HERE = os.path.dirname(os.path.realpath(__file__))
REQ = re.compile(r"\.test_r(\d+)")

def req_of(test_id):
    m = REQ.search(test_id); return f"R{int(m.group(1))}" if m else None

def _ignore(d, names): return [n for n in names if n in (".git", "__pycache__", ".pytest_cache")]

def overlay(dst, *srcs):
    for s in srcs:
        if s and os.path.isdir(s): shutil.copytree(s, dst, dirs_exist_ok=True, ignore=_ignore)

def run_tests(tests_dir, workspace, per_test_s=20, file_timeout_s=300, only=None):
    """Run every test_*.py in tests_dir against a copy of `workspace`. Returns {"tests": {id: {status, req, msg, secs}}, "files": {...}}."""
    tests_dir = os.path.abspath(tests_dir); workspace = os.path.abspath(workspace)
    tmp = tempfile.mkdtemp(prefix="cb-ref-"); ws = os.path.join(tmp, "ws"); out = {"tests": {}, "files": {}}
    try:
        shutil.copytree(workspace, ws, ignore=_ignore)
        files = sorted(f for f in os.listdir(tests_dir) if re.fullmatch(r"test_.*\.py", f) and (not only or f in only))
        env = dict(os.environ, HOME=tmp, PYTHONDONTWRITEBYTECODE="1", PYTHONHASHSEED="0", CODEBOT_REFERENCE_RUN="1")
        env.pop("CODEBOT_TASK_ID", None)
        for fn in files:
            t0 = time.time(); seen = {}
            try:
                p = subprocess.run([sys.executable, "-I", os.path.join(HERE, "refrun.py"), tests_dir, ws, fn, str(per_test_s)], capture_output=True, text=True,
                                   timeout=file_timeout_s, cwd=ws, env=env, stdin=subprocess.DEVNULL); raw, rc, hung = p.stdout, p.returncode, False
            except subprocess.TimeoutExpired as e:
                raw = e.stdout.decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or ""); rc, hung = -9, True
            for ln in raw.splitlines():
                try: r = json.loads(ln)
                except ValueError: continue
                seen[r["id"]] = r
            out["files"][fn] = {"secs": round(time.time() - t0, 1), "rc": rc, "tests_reported": len(seen), "hung": hung}
            declared = _declared(tests_dir, fn)
            broken = [r for tid, r in seen.items() if tid.endswith((".<import>", ".<file>"))]
            why = broken[0]["msg"] if broken else ("the test process hung" if hung else "the test process crashed before reporting" if rc not in (0, None) and not seen else "")
            for tid in broken: seen.pop(tid["id"], None)
            for tid in declared:           # every declared test is accounted for: one that never ran is a failure, so a missing module cannot shrink the denominator
                if tid not in seen: seen[tid] = {"id": tid, "status": "timeout" if hung else "error", "msg": f"not run: {why or 'no result reported'}", "secs": 0}
            if not declared and why: seen[f"{fn[:-3]}.<file>"] = {"id": f"{fn[:-3]}.<file>", "status": "error", "msg": why, "secs": 0}
            for tid, r in seen.items(): out["tests"][tid] = {**r, "req": req_of(tid)}
    finally: shutil.rmtree(tmp, ignore_errors=True)
    return out

def _declared(tests_dir, fn):
    """Test ids a file declares (static scan; used only to account for tests lost when a file hangs)."""
    ids, cls = [], None; mod = fn[:-3]
    try:
        with open(os.path.join(tests_dir, fn)) as f: lines = f.read().splitlines()
    except OSError: return ids
    for ln in lines:
        m = re.match(r"class (\w+)\(", ln)
        if m: cls = m.group(1)
        m = re.match(r"\s+def (test_\w+)\(", ln)
        if m and cls: ids.append(f"{mod}.{cls}.{m.group(1)}")
    return ids

def summarize(res, requirements=None):
    tests = res["tests"]; total = len(tests); passed = sum(1 for t in tests.values() if t["status"] == "pass")
    by = {}
    for t in tests.values():
        r = t.get("req") or "unmapped"; b = by.setdefault(r, {"passed": 0, "total": 0}); b["total"] += 1; b["passed"] += 1 if t["status"] == "pass" else 0
    out = {"total": total, "passed": passed, "failed": sum(1 for t in tests.values() if t["status"] == "fail"), "errors": sum(1 for t in tests.values() if t["status"] in ("error", "timeout")),
           "skipped": sum(1 for t in tests.values() if t["status"] == "skip"), "pass_rate": round(passed / total, 4) if total else 0.0, "by_requirement": by,
           "failing": sorted(i for i, t in tests.items() if t["status"] != "pass")[:60], "files": res["files"]}
    if requirements:
        out["requirements_met"] = sum(1 for r in requirements if by.get(r["id"], {}).get("total") and by[r["id"]]["passed"] == by[r["id"]]["total"])
        out["requirements_total"] = len(requirements)
    return out

def verify(m, workspace, stage=None, cumulative_upto=None, **kw):
    """Reference verification of a workspace. For a growing project `cumulative_upto` = stage index: the tests of every stage up to and including it run
    (earlier requirements stay in effect). Returns summarize() plus the raw per-test results."""
    if m.get("stages") and cumulative_upto is not None:
        res = {"tests": {}, "files": {}}
        for i, st in enumerate(m["stages"][:cumulative_upto + 1]):
            r = run_tests(M.tests_dir(m, st), workspace, **kw)
            for k, v in r["tests"].items(): res["tests"][f"{st['id']}:{k}"] = {**v, "req": f"{st['id']}:{v['req']}" if v.get("req") else None, "stage": st["id"]}
            res["files"].update({f"{st['id']}:{k}": v for k, v in r["files"].items()})
        reqs = [{"id": f"{st['id']}:{r['id']}"} for st in m["stages"][:cumulative_upto + 1] for r in st["requirements"]]
    else:
        res = run_tests(M.tests_dir(m, stage), workspace, **kw); reqs = M.requirements(m, stage)
    s = summarize(res, reqs); s["tests"] = res["tests"]; return s

def build_solution(m, dest):
    """Seed + reference solution (+ explicit deletions) in `dest`: the trusted implementation the reference tests must accept."""
    overlay(dest, M.seed_dir(m), M.solution_dir(m))
    for rel in m.get("solution_deletes", []):
        p = os.path.join(dest, rel)
        if os.path.isfile(p): os.remove(p)

def overlay_stages(dest, m, upto):
    """The reference workspace of a growing project after its first `upto` stages: the seed, then each stage's solution files on top (a stage may delete files of
    the earlier code through `solution_deletes`, e.g. the refactoring stage replaces a module by a package)."""
    overlay(dest, M.seed_dir(m))
    for st in m["stages"][:upto]:
        for rel in st.get("solution_deletes", []):
            q = os.path.join(dest, rel)
            if os.path.isdir(q): shutil.rmtree(q)
            elif os.path.exists(q): os.remove(q)
        overlay(dest, M.solution_dir(m, st))

def validate_level(m, home=None):
    """Self-test of the oracle: reference tests must pass 100% (no skips) on seed+solution, and must NOT pass entirely on the bare seed (otherwise the
    benchmark would score a do-nothing agent as perfect). Requirements flagged `regression: true` are expected to pass on the seed already.
    Returns {"problems": [...], "solution": summary, "seed": summary}."""
    problems = list(M.validate_manifest(m, home)); out = {"problems": problems}
    if m.get("stages"):
        prev = None
        for i, st in enumerate(m["stages"]):
            tmp = tempfile.mkdtemp(prefix="cb-val-")
            try:
                overlay_stages(tmp, m, i + 1)
                sol = verify(m, tmp, cumulative_upto=i); out.setdefault("stages", {})[st["id"]] = {"solution": {k: v for k, v in sol.items() if k != "tests"}}
                if sol["passed"] != sol["total"] or sol["skipped"] or sol["total"] == 0: problems.append(f"{st['id']}: reference tests do not all pass on the reference solution: {sol['failing'][:5]}")
                unm = sol["by_requirement"].get("unmapped")
                if unm: problems.append(f"{st['id']}: {unm['total']} test(s) are not mapped to a requirement (name them test_rNN_...)")
                for r in st["requirements"]:
                    if f"{st['id']}:{r['id']}" not in sol["by_requirement"]: problems.append(f"{st['id']}: requirement {r['id']} has no reference test")
                base = tempfile.mkdtemp(prefix="cb-val-seed-")
                try:
                    overlay_stages(base, m, i)
                    sd = verify(m, base, cumulative_upto=i); out["stages"][st["id"]]["previous_stage_workspace"] = {k: v for k, v in sd.items() if k != "tests"}
                    new_pass = [t for t, v in sd["tests"].items() if v["status"] == "pass" and v.get("stage") == st["id"]]
                    if len(new_pass) == len([1 for v in sd["tests"].values() if v.get("stage") == st["id"]]) and new_pass: problems.append(f"{st['id']}: a workspace that only has the previous stages already passes this stage's tests")
                finally: shutil.rmtree(base, ignore_errors=True)
            finally: shutil.rmtree(tmp, ignore_errors=True)
        return out
    tmp = tempfile.mkdtemp(prefix="cb-val-")
    try:
        build_solution(m, tmp); sol = verify(m, tmp)
        if sol["passed"] != sol["total"] or sol["skipped"] or sol["total"] == 0: problems.append(f"reference tests do not all pass on the reference solution: {sol['failing'][:8]}")
        if "unmapped" in sol["by_requirement"]: problems.append(f"{sol['by_requirement']['unmapped']['total']} test(s) are not mapped to a requirement (name them test_rNN_...)")
        for r in m["requirements"]:
            if r["id"] not in sol["by_requirement"]: problems.append(f"requirement {r['id']} has no reference test")
        seed = tempfile.mkdtemp(prefix="cb-val-seed-")
        try:
            overlay(seed, M.seed_dir(m)); sd = verify(m, seed)
            if sd["passed"] == sd["total"]: problems.append("the bare seed already passes every reference test: a do-nothing agent would score 100%")
            for r in m["requirements"]:
                b = sd["by_requirement"].get(r["id"], {"passed": 0, "total": 0})
                if r.get("regression") and b["passed"] != b["total"]: problems.append(f"{r['id']} is a regression requirement but fails on the seed")
                if not r.get("regression") and b["total"] and b["passed"] == b["total"]: problems.append(f"{r['id']} already passes on the bare seed (flag it `regression` or make the test discriminating)")
        finally: shutil.rmtree(seed, ignore_errors=True)
        out["solution"] = {k: v for k, v in sol.items() if k != "tests"}; out["seed"] = {k: v for k, v in sd.items() if k != "tests"}
    finally: shutil.rmtree(tmp, ignore_errors=True)
    return out

def validate_plan(m, home, plan=None, runner=None):
    """A reference plan is VALIDATED when every task's assertions are usable, FAIL on the bare seed (rc 1: a real test that fails today) and HOLD on the trusted
    solution (rc 0). Returns a list of problems (empty = validated). Used by the framework tests and `codebot bench validate`."""
    from .. import assertions as A, checkspec as K
    path = M.plan_path(m)
    if plan is None and not path: return ["no reference plan"]
    if plan is None:
        with open(path) as f: plan = json.load(f)
    runner = runner or os.path.join(home, "modes", "code", "verifiers", "assert_runner.py"); problems = []
    seed, sol = tempfile.mkdtemp(prefix="cb-vp-seed-"), tempfile.mkdtemp(prefix="cb-vp-sol-"); spec = os.path.join(tempfile.mkdtemp(prefix="cb-vp-"), "spec.json")
    try:
        overlay(seed, M.seed_dir(m)); build_solution(m, sol)
        for i, t in enumerate(plan["tasks"], 1):
            asserts = K.prepare(t.get("asserts")); err = A.validate(asserts)
            if err: problems.append(f"task {i}: {err}"); continue
            with open(spec, "w") as f: json.dump({"asserts": asserts}, f)
            rs = subprocess.run([sys.executable, runner, spec], cwd=seed, capture_output=True, text=True, timeout=300).returncode
            rv = subprocess.run([sys.executable, runner, spec], cwd=sol, capture_output=True, text=True, timeout=300)
            if rs != 1: problems.append(f"task {i} ({t['title']}): on the bare seed the assertions give rc {rs}, expected 1 (a real failing test)")
            if rv.returncode != 0: problems.append(f"task {i} ({t['title']}): the trusted solution fails them: {rv.stdout.strip()[-200:]}")
        if len(plan["tasks"]) < M.manifest_items(m): problems.append("the plan has fewer tasks than the spec has numbered items")
    finally:
        for d in (seed, sol, os.path.dirname(spec)): shutil.rmtree(d, ignore_errors=True)
    return problems
