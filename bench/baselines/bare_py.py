"""The restart loop of bare3.py for the Python levels of the ladder (2026-10-19), to see whether the calc_go results hold elsewhere. The model gets
SPEC.md and writes every module whole: a reply gives each file as a line `### name.py` followed by one ```python block. Only the modules the spec
names are taken, the last block for a file wins, and a file the reply leaves out keeps its last version. A version builds when every one of them
exists and compiles. Feedback: the compiler's error, or the spec's own examples (grounding.all_examples, the ones codebot derives) run by codebot's
assert runner, whose FAIL lines go back as they are. No planner, no tools, no model-written checks, no hidden tests. Restarts and the delivered
version as in bare3.py; model and settings from bare2.py (its chat()). Every round's whole program is saved in round-NN/code/.
Usage: bare_py.py LEVEL OUT_DIR --seed N [--minutes 45] [--patience 2] [--max-rounds 60] [--fake REPLY_FILE ...]"""
import argparse, json, os, re, shutil, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bare2 as B                                                   # also puts lib/ on the path
from codebot import checkspec as K, grounding

RUNNER = os.path.join(B.HOME, "modes/code/verifiers/assert_runner.py")
SYSTEM = ("You are an expert Python programmer. You write complete, correct, working code. Reply with the complete contents of every file, "
          "each as a line `### <file name>` followed by one ```python code block, and nothing else.")
NAME = r"([A-Za-z_]\w*\.py)"
HEAD = re.compile(r"^[#*\s]*(?:file:?\s*)?`?" + NAME + r"`?\**:?\s*$", re.I)       # "### a.py", "**`a.py`**", "File: a.py", "a.py:"
FIRST = re.compile(r"^#\s*(?:file:?\s*)?" + NAME + r"\s*$", re.I)                   # "# a.py" as the first line of a block

def load(level):
    cat = os.path.join(B.HOME, "bench/ladder/catalog", level); spec = open(os.path.join(cat, "SPEC.md")).read()
    return {"cat": cat, "spec": spec, "files": sorted(set(re.findall("`" + NAME + "`", spec))), "checks": K.prepare(grounding.all_examples(spec))}

def show(files):
    return "\n\n".join(f"### {n}\n```python\n{files[n].rstrip()}\n```" for n in sorted(files))

def first_prompt(lv):
    return (f"Write the code this specification describes.\n\nThe code directory contains only README.md.\n\nSpecification (SPEC.md):\n\n"
            f"{lv['spec'].strip()}\n\nReply with the complete contents of every file ({', '.join(lv['files'])}).")

def repair_prompt(lv, files, feedback):
    have = f"these files:\n\n{show(files)}" if files else "no code yet."
    return (f"Specification (SPEC.md):\n\n{lv['spec'].strip()}\n\nThe code directory contains README.md and {have}\n\n{feedback}\n\n"
            "Fix the code so that it meets the whole specification. Reply with the complete corrected contents of every file.")

def extract(reply, names):
    """{file name: code} for the spec's modules. parts[1], parts[3], ... are the insides of code blocks; with an odd number of fences the last
    block was cut off and is kept as it is. A block is named by the line before it, else by a `# name.py` first line, else it is the only
    module when the spec names one. The last block for a file wins (the model often drafts, then rewrites)."""
    parts, out = reply.split("```"), {}
    if len(parts) == 1 and len(names) == 1 and reply.lstrip().startswith(("import", "from", "def", "class", "#", '"""')):
        return {names[0]: reply}
    for i in range(1, len(parts), 2):
        lang, _, code = parts[i].partition("\n")
        if lang.strip().lower() not in ("", "python", "py", "python3"): continue
        before = [l for l in parts[i - 1].split("\n") if l.strip()]
        m = (HEAD.match(before[-1].strip()) if before else None) or FIRST.match(code.lstrip().split("\n", 1)[0].strip())
        name = m.group(1) if m else (names[0] if len(names) == 1 else None)
        if name in names: out[name] = code
    return out

def label(a):
    return f"{a['func']}({', '.join(repr(x) for x in a.get('args', []))})"[:120] if a["type"] == "call" else " ".join(a.get("argv", []))[:120]

def run_examples(ws, run, checks):
    """(failed, runner output). The runner makes the calls in its own process with no time limit, so a run that hangs (a call that never
    returns, or an is_prime that counts to n) is redone one example at a time, 20 s each."""
    spec = os.path.join(run, "checks.json")
    with open(spec, "w") as f: json.dump({"asserts": checks}, f)
    try:
        r = subprocess.run([sys.executable, RUNNER, spec], cwd=ws, capture_output=True, text=True, timeout=120)
        if r.returncode == 0: return 0, r.stdout.strip()
        m = re.search(r"(\d+) of \d+ assertions failed", r.stdout)
        return (int(m.group(1)) if m else len(checks)), (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired: pass
    fails = []
    for a in checks:
        with open(spec, "w") as f: json.dump({"asserts": [a]}, f)
        try: r = subprocess.run([sys.executable, RUNNER, spec], cwd=ws, capture_output=True, text=True, timeout=20)
        except subprocess.TimeoutExpired: fails.append(f"FAIL: {label(a)} did not finish within 20 seconds"); continue
        if r.returncode: fails += [l for l in r.stdout.splitlines() if l.startswith("FAIL: ")] or [f"FAIL: {label(a)}"]
    return len(fails), "\n".join(fails) + f"\n{len(fails)} of {len(checks)} assertions failed"

def evaluate(lv, run, files):
    """(built, passed, feedback) for one version, judged in a fresh copy of the seed."""
    ws = os.path.join(run, "ws"); shutil.rmtree(ws, ignore_errors=True); shutil.copytree(os.path.join(lv["cat"], "seed"), ws)
    for n, code in files.items():
        with open(os.path.join(ws, n), "w") as f: f.write(code)
    missing = [n for n in lv["files"] if n not in files]
    if missing: return False, 0, f"These files are missing: {', '.join(missing)}."
    errs = [p.stderr.strip()[-1500:] for p in (subprocess.run([sys.executable, "-m", "py_compile", n], cwd=ws, capture_output=True, text=True, timeout=60)
                                                for n in sorted(files)) if p.returncode]
    if errs: return False, 0, "The code does not compile:\n```\n" + "\n\n".join(errs)[-3000:] + "\n```"
    total = len(lv["checks"]); failed, out = run_examples(ws, run, lv["checks"])
    if not failed: return True, total, ""
    out = out if len(out) <= 6000 else out[:6000] + "\n... (more failures cut)"
    return True, total - failed, f"All files compile, but {failed} of {total} examples from the specification fail:\n{out}"

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("level"); ap.add_argument("out"); ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--minutes", type=float, default=45); ap.add_argument("--patience", type=int, default=2)
    ap.add_argument("--max-rounds", type=int, default=60); ap.add_argument("--fake", nargs="*")
    a = ap.parse_args(); lv = load(a.level); run = os.path.abspath(a.out); os.makedirs(run, exist_ok=True); total = len(lv["checks"])
    log = open(os.path.join(run, "rounds.jsonl"), "a"); t0 = time.time()
    rnd, draft, fresh, stale, dbest, best, stop = 0, 0, True, 0, None, None, "time cap"
    while True:
        if time.time() - t0 > a.minutes * 60: stop = "time cap"; break
        if rnd >= a.max_rounds: stop = "round cap"; break
        rnd += 1
        if fresh: draft += 1; stale = 0; dbest = None; user = first_prompt(lv)
        else: user = repair_prompt(lv, dbest[1], dbest[2])
        if a.fake: r = {"content": open(a.fake[min(rnd, len(a.fake)) - 1]).read(), "secs": 0, "thermal_wait": 0, "prompt_tokens": 0, "gen_tokens": 0, "done_reason": "fake"}
        else: r = B.chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}], a.seed * 1000 + rnd)
        d = os.path.join(run, f"round-{rnd:02d}"); os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "reply.md"), "w") as f: f.write(r["content"])
        with open(os.path.join(d, "prompt.md"), "w") as f: f.write(user)
        got = extract(r["content"], lv["files"]); base = dbest[1] if dbest else {}
        rec = {"round": rnd, "draft": draft, "fresh": fresh, **{k: r[k] for k in ("secs", "thermal_wait", "prompt_tokens", "gen_tokens", "done_reason")}}
        if not got:
            score, files, fb = (False, -1), base, "Your last reply did not contain any file as a line `### <file name>` followed by a ```python code block."
            rec.update(code=False)
        else:
            files = {**base, **got}; os.makedirs(os.path.join(d, "code"), exist_ok=True)
            for n, code in files.items():
                with open(os.path.join(d, "code", n), "w") as f: f.write(code)
            built, passed, fb = evaluate(lv, run, files)
            score = (built, passed); rec.update(code=True, files=sorted(got), chars=sum(map(len, got.values())), built=built, examples=passed)
            if built and (best is None or passed >= best[1]): best = (rnd, passed)
        if r["done_reason"] == "length": fb = "Your last reply was cut off because it was too long.\n\n" + fb
        improved = dbest is None or score > dbest[0]
        if improved or score == dbest[0]: dbest = (score, files, fb)          # continue from the draft's best version (latest among equals)
        stale = 0 if improved else stale + 1
        fresh = stale >= a.patience
        rec.update(improved=improved, stale=stale, restart_next=fresh, elapsed=round(time.time() - t0, 1))
        log.write(json.dumps(rec) + "\n"); log.flush(); print(json.dumps(rec), flush=True)
        if rec.get("built") and rec.get("examples") == total: stop = "examples pass"; break
    summary = {"level": a.level, "seed": a.seed, "rounds": rnd, "drafts": draft, "stop": stop, "minutes": round((time.time() - t0) / 60, 1),
               "examples_total": total, "files": lv["files"], "feedback": "examples", "patience": a.patience,
               "delivered_round": best[0] if best else None, "delivered_examples": best[1] if best else 0,
               "model": B.MODEL, "temperature": B.TEMPERATURE, "num_predict": B.NUM_PREDICT, "think": False}
    with open(os.path.join(run, "summary.json"), "w") as f: json.dump(summary, f, indent=1)
    print(json.dumps(summary), flush=True)

if __name__ == "__main__": main()
