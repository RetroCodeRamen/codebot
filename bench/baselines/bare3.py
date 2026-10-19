"""bare2.py plus restarts. A draft starts from the spec alone; each repair round continues from the draft's best version. When `patience` rounds
in a row do not improve the draft (by the loop's own signal: it builds, then spec examples passed), the next round starts a fresh draft. The
delivered version is the best of all drafts by that signal (latest among equals). Feedback: compiler output and the 15 spec examples only.
Why (2026-10-19): in 9 runs of bare.py/bare2.py, 5 stalled on a broken first draft and sent back the identical reply round after round, while the
own signal told good runs (12-13/15 examples -> 22-26/32 hidden) from stuck ones (0-1/15 -> 7-10/32).
Usage: bare3.py OUT_DIR --seed N [--minutes 45] [--patience 2] [--max-rounds 60] [--fake REPLY_FILE ...]"""
import argparse, json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bare2 as B

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("out"); ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--minutes", type=float, default=45); ap.add_argument("--patience", type=int, default=2)
    ap.add_argument("--max-rounds", type=int, default=60); ap.add_argument("--fake", nargs="*")
    a = ap.parse_args(); run = os.path.abspath(a.out); os.makedirs(run, exist_ok=True); total = len(B.EXAMPLES)
    log = open(os.path.join(run, "rounds.jsonl"), "a"); t0 = time.time()
    rnd, draft, fresh, stale, dbest, best, stop = 0, 0, True, 0, None, None, "time cap"
    while True:
        if time.time() - t0 > a.minutes * 60: stop = "time cap"; break
        if rnd >= a.max_rounds: stop = "round cap"; break
        rnd += 1
        if fresh: draft += 1; stale = 0; dbest = None; user = B.first_prompt()
        else: user = B.repair_prompt(dbest[1], dbest[2])
        if a.fake: r = {"content": open(a.fake[min(rnd, len(a.fake)) - 1]).read(), "secs": 0, "thermal_wait": 0, "prompt_tokens": 0, "gen_tokens": 0, "done_reason": "fake"}
        else: r = B.chat([{"role": "system", "content": B.SYSTEM}, {"role": "user", "content": user}], a.seed * 1000 + rnd)
        d = os.path.join(run, f"round-{rnd:02d}"); os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "reply.md"), "w") as f: f.write(r["content"])
        with open(os.path.join(d, "prompt.md"), "w") as f: f.write(user)
        new = B.extract(r["content"])
        rec = {"round": rnd, "draft": draft, "fresh": fresh, **{k: r[k] for k in ("secs", "thermal_wait", "prompt_tokens", "gen_tokens", "done_reason")}}
        if new is None:
            score, new, fb = (False, -1), "", "Your last reply did not contain main.go in a ```go code block."
            rec.update(code=False)
        else:
            with open(os.path.join(d, "main.go"), "w") as f: f.write(new)
            built, passed, fb = B.evaluate(run, new)
            if r["done_reason"] == "length": fb = "Your last reply was cut off because it was too long.\n\n" + fb
            score = (built, passed); rec.update(code=True, chars=len(new), built=built, examples=passed)
            if built and (best is None or passed >= best[1]): best = (rnd, passed)
        improved = dbest is None or score > dbest[0]
        if improved or score == dbest[0]: dbest = (score, new, fb)          # continue from the draft's best version (latest among equals)
        stale = 0 if improved else stale + 1
        fresh = stale >= a.patience
        rec.update(improved=improved, stale=stale, restart_next=fresh, elapsed=round(time.time() - t0, 1))
        log.write(json.dumps(rec) + "\n"); log.flush(); print(json.dumps(rec), flush=True)
        if rec.get("built") and rec.get("examples") == total: stop = "examples pass"; break
    summary = {"seed": a.seed, "rounds": rnd, "drafts": draft, "stop": stop, "minutes": round((time.time() - t0) / 60, 1), "examples_total": total,
               "feedback": "examples", "patience": a.patience, "delivered_round": best[0] if best else None, "delivered_examples": best[1] if best else 0,
               "model": B.MODEL, "temperature": B.TEMPERATURE, "num_predict": B.NUM_PREDICT, "think": False}
    with open(os.path.join(run, "summary.json"), "w") as f: json.dump(summary, f, indent=1)
    print(json.dumps(summary), flush=True)

if __name__ == "__main__": main()
