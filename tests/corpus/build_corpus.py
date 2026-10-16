"""Builds the replay corpus (tests/corpus/*.json) from the real model outputs kept in projects/ (git-ignored, so the corpus is the durable copy).
Run by hand after a real-model run:  python3 tests/corpus/build_corpus.py
Read-only on projects/ and bench/. Every assertion is judged against the spec's reference solution and against the bare seed, so a test can tell a
salvage rule that drops a SOUND assertion (bad) from one that drops an unsound or impossible one (good). The model-written `run` commands in this
corpus were reviewed by hand on 2026-10-16 (they only import or run the spec's own modules)."""
import glob, hashlib, json, os, re, shutil, subprocess, sys, tempfile

HOME = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUT = os.path.dirname(os.path.realpath(__file__))
SUITES = {  # spec file -> (suite name, dirs copied into the workspace for the 'reference' verdict, dirs for the 'seed' verdict)
    "bench/suite/pyutils/spec.md": ("pyutils", ["bench/suite/pyutils/seed", "bench/suite/pyutils/reference"], ["bench/suite/pyutils/seed"]),
    "bench/ladder/catalog/level_01/SPEC.md": ("level_01", ["bench/ladder/catalog/level_01/seed", "bench/ladder/catalog/level_01/reference_solution"], ["bench/ladder/catalog/level_01/seed"]),
}

def _md5(path):
    with open(path, "rb") as f: return hashlib.md5(f.read()).hexdigest()

def suite_of(project_dir):
    spec = os.path.join(project_dir, "SPEC.md")
    if not os.path.isfile(spec): return None
    h = _md5(spec)
    return next((v for k, v in SUITES.items() if _md5(os.path.join(HOME, k)) == h), None)

def model_of(name): return "qwen" if "qwen" in name else "ornith"

def judge(asserts, dirs):
    """[fail message or None] per assertion, each evaluated on its own in a fresh copy of dirs."""
    with tempfile.TemporaryDirectory() as d:
        for src in dirs: shutil.copytree(os.path.join(HOME, src), d, dirs_exist_ok=True)
        spec = os.path.join(d, "_corpus_asserts.json")
        with open(spec, "w") as f: json.dump(asserts, f)
        code = ("import json, sys; sys.path.insert(0, %r); from codebot import assertions as A\n"
                "out = []\nfor a in json.load(open('_corpus_asserts.json')):\n"
                "    try: m = A.check([a])\n    except BaseException as e: m = [f'harness error: {type(e).__name__}: {e}']\n"
                "    out.append(m[0] if m else None)\nprint(json.dumps(out))\n") % os.path.join(HOME, "lib")
        p = subprocess.run([sys.executable, "-c", code], cwd=d, capture_output=True, text=True, timeout=600)
        return json.loads(p.stdout.strip().splitlines()[-1])

def collect_assertions():
    rows = {}
    for pdir in sorted(glob.glob(os.path.join(HOME, "projects", "*"))):
        su = suite_of(pdir)
        if not su: continue
        name = os.path.basename(pdir)
        for sf in sorted(glob.glob(os.path.join(pdir, "checks", "*.spec.json"))):
            tid = os.path.basename(sf)[:-len(".spec.json")]
            try:
                with open(sf) as f: asserts = json.load(f).get("asserts") or []
            except (OSError, ValueError): continue
            for a in asserts:
                key = (su[0], json.dumps(a, sort_keys=True))
                r = rows.setdefault(key, {"suite": su[0], "assert": a, "sources": []})
                r["sources"].append({"project": name, "task": tid, "model": model_of(name), "actor": "splitter" if "." in tid else "planner"})
    by_suite = {}
    for (s, _), r in rows.items(): by_suite.setdefault(s, []).append(r)
    out = []
    for s, rs in sorted(by_suite.items()):
        _, ref_dirs, seed_dirs = next(v for v in SUITES.values() if v[0] == s)
        ref = judge([r["assert"] for r in rs], ref_dirs); seed = judge([r["assert"] for r in rs], seed_dirs)
        for r, m, sm in zip(rs, ref, seed):
            r["reference"] = "holds" if m is None else "fails"; r["seed"] = "holds" if sm is None else "fails"
            if m: r["reference_msg"] = m[:200]
            out.append(r)
    return out

def collect_plan_replies():
    out = []
    for f in sorted(glob.glob(os.path.join(HOME, "projects", "*", "plan.reply.*.json"))):
        pdir = os.path.dirname(f); su = suite_of(pdir); n = int(re.search(r"plan\.reply\.(\d+)\.json$", f).group(1))
        with open(f) as fh: raw = fh.read()
        out.append({"suite": su[0] if su else None, "project": os.path.basename(pdir), "round": n, "model": model_of(os.path.basename(pdir)), "raw": raw})
    return out

def main():
    a = collect_assertions(); p = collect_plan_replies()
    with open(os.path.join(OUT, "assertions.json"), "w") as f: json.dump(a, f, indent=1, ensure_ascii=False)
    with open(os.path.join(OUT, "plan_replies.json"), "w") as f: json.dump(p, f, indent=1, ensure_ascii=False)
    sound = sum(r["reference"] == "holds" for r in a)
    print(f"{len(a)} distinct assertions ({sound} hold on the reference, {len(a) - sound} do not), {len(p)} raw plan replies")

if __name__ == "__main__": main()
