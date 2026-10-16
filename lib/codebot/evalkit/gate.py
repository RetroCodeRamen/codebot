"""Compatibility and speed gate for one model (about 5-10 min): does it load on this Ollama build, how much of it lands on the GPU, how fast are
prompt processing and generation, and does it produce valid output under codebot's two real schemas (a planner task, a worker action)?
A model that fails here is not worth a planner probe or a worker run. Results: bench/results/eval/gate-<stamp>.json."""
import json, os, time, urllib.request
from .. import assertions as A, checkspec as K, ollama, planner as PL, thermal, worker as WK
from ..capbench import manifests as M

WORKER_PROMPT = ("TASK T1: Implement slugify(text) in textutil.py: lowercase, every run of characters that are not ASCII letters or digits becomes one hyphen, "
                 "leading and trailing hyphens removed. slugify(\"Hello World\") is \"hello-world\".\nCHECK: the acceptance check fails: textutil.py does not exist.\n"
                 "FILES: (none yet)\n\nReply with ONE JSON action.")

def _ps(proj):
    try:
        with urllib.request.urlopen(ollama.base_url(proj) + "/api/ps", timeout=10) as r: return json.loads(r.read().decode()).get("models", [])
    except OSError: return []

def unload(proj, model):
    try:
        req = urllib.request.Request(ollama.base_url(proj) + "/api/generate", data=json.dumps({"model": model, "keep_alive": 0}).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r: r.read()
    except OSError: pass

def run(proj, model, think=False, n=3, log=print):
    """One model through the gate. proj supplies the server port and num_ctx. Returns a result dict (never raises for a model problem)."""
    m = M.load(proj.home, "level_01"); spec = open(M.spec_path(m)).read(); items = PL.item_texts(spec)
    row = {"model": model, "think": think, "ok": False, "errors": [], "temp_max": None}
    temps = []; t0 = time.time()
    def call(messages, schema, num_predict):
        t = thermal.read_temp(); temps.append(t) if t is not None else None
        r = ollama.chat(proj, messages, schema=schema, model=model, temperature=0.2, num_predict=num_predict, timeout=1800, retries=1, think=think, role="gate")
        t = thermal.read_temp(); temps.append(t) if t is not None else None
        return r
    try:
        r = call([{"role": "user", "content": "Reply with the JSON {\"ok\": true}."}], {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}, 20)
        row["load_s"] = round(r["load_ms"] / 1000, 1)
        ps = next((x for x in _ps(proj) if x.get("name", "").startswith(model.split(":")[0]) or x.get("model") == model), None)
        if ps and ps.get("size"): row.update(size_gb=round(ps["size"] / 1e9, 2), gpu_share=round(ps.get("size_vram", 0) / ps["size"], 2))
        plans, works, speeds = [], [], []
        head = f"SPECIFICATION\n{spec}\n\nCODE DIRECTORY (read only, current files):\n(empty)\n\n"
        for k in range(n):
            n_item = [1, 3, 7][k % 3]
            user = head + f"Plan exactly ONE task: the task for item {n_item} of {len(items)}:\n{items[n_item - 1]}\n\nIts assertions test only what item {n_item} says. Reply with the JSON task."
            r = call([{"role": "system", "content": PL.GUIDE_ITEM}, {"role": "user", "content": user}], PL.task_schema(), 2500)
            speeds.append(r)
            try:
                t = json.loads(r["content"]); a = K.prepare(t.get("asserts"))
                plans.append({"valid_json": True, "asserts": len(a), "usable": sum(1 for x in a if A.validate([x]) is None), "secs": r["secs"]})
            except ValueError: plans.append({"valid_json": False, "secs": r["secs"], "done_reason": r.get("done_reason")})
        for k in range(n):
            r = call([{"role": "system", "content": WK.SYSTEM}, {"role": "user", "content": WORKER_PROMPT}], WK.action_schema(), 1800)
            speeds.append(r)
            try: a = json.loads(r["content"]); WK.validate_shape(a); works.append({"valid": True, "action": a.get("action"), "secs": r["secs"]})
            except (ValueError, WK.ActionError) as e: works.append({"valid": False, "error": str(e)[:120], "secs": r["secs"]})
        pt = sum(x["prompt_tokens"] for x in speeds); pm = sum(x["prompt_ms"] for x in speeds); gt = sum(x["gen_tokens"] for x in speeds); gm = sum(x["gen_ms"] for x in speeds)
        row.update(prompt_tps=round(pt / (pm / 1000), 1) if pm else None, gen_tps=round(gt / (gm / 1000), 1) if gm else None,
                   planner_calls=plans, worker_calls=works, planner_valid=sum(1 for p in plans if p.get("valid_json") and p.get("usable")), worker_valid=sum(1 for w in works if w["valid"]),
                   thinking=think)
        row["ok"] = row["planner_valid"] == n and row["worker_valid"] == n
    except ollama.ModelError as e: row["errors"].append(str(e)[:300])
    row["secs"] = round(time.time() - t0); row["temp_max"] = max(temps) if temps else None
    unload(proj, model)
    log(f"gate {model} think={think}: ok={row['ok']} load {row.get('load_s')} s, gpu {row.get('gpu_share')}, prompt {row.get('prompt_tps')} tok/s, gen {row.get('gen_tps')} tok/s, "
        f"planner {row.get('planner_valid')}/{n}, worker {row.get('worker_valid')}/{n}, max {row['temp_max']} C" + (f", errors {row['errors']}" if row["errors"] else ""))
    return row
