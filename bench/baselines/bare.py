"""Bare-model baseline for calc_go. The model gets SPEC.md and writes main.go whole. The only feedback is the Go compiler's output and the spec's
own examples (exactly the 15 that codebot's grounding derives, compared the way codebot's runner compares them). No planner, no tools, no checks
written by the model, no hidden tests. Each round is a fresh prompt: spec + current main.go + what failed. Stops when the build succeeds and every
example passes, or at the round/time cap. The delivered version is the best one by the loop's own signal (built, most examples passing, latest wins).
Usage: bare.py OUT_DIR --seed N [--rounds 12] [--minutes 45] [--fake REPLY_FILE ...]   (--fake replays files instead of calling the model)"""
import argparse, json, os, re, shutil, subprocess, sys, time, urllib.request

HOME = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the repo root
sys.path.insert(0, os.path.join(HOME, "lib"))
from codebot import grounding, thermal

CAT = os.path.join(HOME, "bench/ladder/catalog/calc_go")
PORT, MODEL, NUM_CTX, TEMPERATURE, NUM_PREDICT = 11500, "codebot-model", 32768, 0.2, 6000
SPEC = open(os.path.join(CAT, "SPEC.md")).read()
GOMOD = open(os.path.join(CAT, "seed/go.mod")).read()
EXAMPLES = [a["argv"][1:] + [a["stdout"]] for a in grounding.all_examples(SPEC) if a["argv"][0] == "./calc"]   # [args..., expected stdout]

SYSTEM = ("You are an expert Go programmer. You write complete, correct, working programs. "
          "Reply with the complete contents of the file main.go in one ```go code block, and nothing else.")

def first_prompt():
    return (f"Write the program this specification describes.\n\nThe code directory already contains go.mod:\n```\n{GOMOD.strip()}\n```\n\n"
            f"Specification (SPEC.md):\n\n{SPEC.strip()}\n\nReply with the complete main.go.")

def repair_prompt(code, feedback):
    return (f"Specification (SPEC.md):\n\n{SPEC.strip()}\n\nThe code directory contains go.mod (`module calc`, `go 1.24`) and this main.go:\n"
            f"```go\n{code.rstrip()}\n```\n\n{feedback}\n\nFix main.go so that it meets the whole specification. Reply with the complete corrected main.go.")

def chat(messages, seed):
    t = thermal.read_temp(); waited = 0
    if t is not None and t >= 92:                              # same rule as codebot's THERMAL_GUARD: pause at 92 C, resume at 84 C
        while waited < 1800:
            time.sleep(20); waited += 20; t = thermal.read_temp()
            if t is None or t <= 84: break
    body = {"model": MODEL, "messages": messages, "stream": False, "think": False, "keep_alive": "30m",
            "options": {"num_ctx": NUM_CTX, "temperature": TEMPERATURE, "num_predict": NUM_PREDICT, "seed": seed}}
    t0 = time.time()
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/api/chat", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=3600) as r: out = json.loads(r.read().decode())
    return {"content": (out.get("message") or {}).get("content", ""), "secs": round(time.time() - t0, 1), "thermal_wait": waited,
            "prompt_tokens": out.get("prompt_eval_count", 0), "gen_tokens": out.get("eval_count", 0), "done_reason": out.get("done_reason", "")}

def extract(reply):
    blocks = re.findall(r"```(?:go|golang)?[ \t]*\n(.*?)```", reply, re.S)
    if not blocks:
        m = re.search(r"```(?:go|golang)?[ \t]*\n(.*)$", reply, re.S)        # a reply cut off inside its code block
        blocks = [m.group(1)] if m else ([reply] if reply.lstrip().startswith(("package", "//")) else [])
    for b in blocks:
        if "package main" in b: return b
    return blocks[0] if blocks else None

def go_env(run):
    return dict(os.environ, PATH="/usr/local/go/bin:" + os.environ.get("PATH", ""), GOCACHE=os.path.join(run, ".gocache"),
                GOPATH=os.path.join(run, ".gopath"), GOTOOLCHAIN="local", GOFLAGS="-mod=mod", CGO_ENABLED="0", HOME=run)

def evaluate(run, code):
    """(built, passed, feedback) for one main.go, judged in a fresh copy of the seed."""
    ws = os.path.join(run, "ws"); shutil.rmtree(ws, ignore_errors=True); shutil.copytree(os.path.join(CAT, "seed"), ws)
    with open(os.path.join(ws, "main.go"), "w") as f: f.write(code)
    p = subprocess.run(["go", "build", "-o", "calc", "."], cwd=ws, capture_output=True, text=True, env=go_env(run), timeout=300)
    if p.returncode != 0:
        return False, 0, f"`go build -o calc .` failed:\n```\n{(p.stdout + p.stderr).strip()[-2000:]}\n```"
    bad = []
    for ex in EXAMPLES:
        args, want = ex[:-1], ex[-1]; cmd = "./calc " + " ".join(args)
        try: q = subprocess.run([os.path.join(ws, "calc"), *args], cwd=ws, input="", capture_output=True, text=True, timeout=10, env=go_env(run))
        except subprocess.TimeoutExpired: bad.append(f"- `{cmd}` did not finish within 10 seconds"); continue
        if q.returncode != 0: bad.append(f"- `{cmd}` exited {q.returncode}, expected 0" + (f" (stderr: {q.stderr.strip()[-150:]})" if q.stderr.strip() else ""))
        elif q.stdout.strip() != want.strip(): bad.append(f"- `{cmd}` printed {q.stdout.strip()[:110]!r}, expected {want!r}")
    passed = len(EXAMPLES) - len(bad)
    if not bad: return True, passed, ""
    return True, passed, "`go build -o calc .` succeeds, but these examples from the specification fail:\n" + "\n".join(bad)

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("out"); ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--rounds", type=int, default=12); ap.add_argument("--minutes", type=float, default=45); ap.add_argument("--fake", nargs="*")
    a = ap.parse_args(); run = os.path.abspath(a.out); os.makedirs(run, exist_ok=True)
    log = open(os.path.join(run, "rounds.jsonl"), "a"); t0 = time.time(); code, feedback, best, stop, done = None, None, None, "round cap", 0
    for rnd in range(1, a.rounds + 1):
        if time.time() - t0 > a.minutes * 60: stop = "time cap"; break
        user = first_prompt() if code is None else repair_prompt(code, feedback)
        if a.fake: r = {"content": open(a.fake[min(rnd, len(a.fake)) - 1]).read(), "secs": 0, "thermal_wait": 0, "prompt_tokens": 0, "gen_tokens": 0, "done_reason": "fake"}
        else: r = chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}], a.seed)
        d = os.path.join(run, f"round-{rnd:02d}"); os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "reply.md"), "w") as f: f.write(r["content"])
        with open(os.path.join(d, "prompt.md"), "w") as f: f.write(user)
        new = extract(r["content"]); rec = {"round": rnd, **{k: r[k] for k in ("secs", "thermal_wait", "prompt_tokens", "gen_tokens", "done_reason")}}
        if new is None:
            rec.update(code=False); feedback = "Your last reply did not contain main.go in a ```go code block."
            if code is None: code = ""
        else:
            code = new
            with open(os.path.join(d, "main.go"), "w") as f: f.write(code)
            built, passed, feedback = evaluate(run, code)
            if r["done_reason"] == "length": feedback = "Your last reply was cut off because it was too long.\n\n" + feedback
            rec.update(code=True, chars=len(code), built=built, examples=passed)
            if built and (best is None or passed >= best[1]): best = (rnd, passed)
        done = rnd; rec["elapsed"] = round(time.time() - t0, 1); log.write(json.dumps(rec) + "\n"); log.flush()
        print(json.dumps(rec), flush=True)
        if rec.get("built") and rec.get("examples") == len(EXAMPLES): stop = "examples pass"; break
    summary = {"seed": a.seed, "rounds": done, "stop": stop, "minutes": round((time.time() - t0) / 60, 1), "examples_total": len(EXAMPLES),
               "delivered_round": best[0] if best else None, "delivered_examples": best[1] if best else 0, "model": MODEL, "temperature": TEMPERATURE,
               "num_predict": NUM_PREDICT, "think": False}
    with open(os.path.join(run, "summary.json"), "w") as f: json.dump(summary, f, indent=1)
    print(json.dumps(summary), flush=True)

if __name__ == "__main__": main()
