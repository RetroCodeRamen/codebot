"""Preflight: prove, against the LIVE model server, that the context setup is what the code assumes it is.
Each item is PASS / FAIL / WARN with the numbers behind it. Also calibrates the chars-per-token ratio (Ollama has no tokenizer endpoint)."""
import datetime, json, os, random, re, time, urllib.request
from . import ollama, worker, tokens, thermal
from .config import now

def _get(proj, path):
    with urllib.request.urlopen(ollama.base_url(proj) + path, timeout=15) as r: return json.loads(r.read().decode())

def _static(proj, home):
    items = []
    try:
        with open(os.path.join(home, "config", "Modelfile")) as f: mf = int(re.search(r"PARAMETER num_ctx (\d+)", f.read()).group(1))
        ocs = []
        for fn in ("opencode.json", "opencode-re.json"):
            p = os.path.join(home, "config", fn)
            if os.path.exists(p):
                with open(p) as f: ocs.append(json.load(f)["provider"]["ollama"]["models"]["codebot-model"]["limit"]["context"])
        vals = {mf, tokens.num_ctx(proj), *ocs}
        items.append(("num_ctx explicit and identical (Modelfile, NUM_CTX, opencode configs)", "PASS" if len(vals) == 1 else "FAIL", f"Modelfile={mf} NUM_CTX={tokens.num_ctx(proj)} opencode={ocs}"))
    except (OSError, AttributeError, KeyError, ValueError) as e: items.append(("num_ctx explicit and identical", "FAIL", f"cannot read config: {e}"))
    ka = proj.get("KEEP_ALIVE", "")
    items.append(("keep_alive configured and sent on every request", "PASS" if re.fullmatch(r"\d+[mh]", ka or "") else "FAIL", f"KEEP_ALIVE={ka!r}"))
    items.append(("thermal sensor readable", "PASS" if thermal.read_temp() is not None else "WARN", f"{thermal.read_temp()} C; THERMAL_GUARD={proj.get('THERMAL_GUARD', '0')} max={proj.get('THERMAL_MAX', 92)} resume={proj.get('THERMAL_RESUME', 84)}"))
    return items

def _sample_text(home, n=2400):
    """Representative cold text: repo code + JSON, salted so no earlier request can have cached it."""
    chunks = []
    for rel in ("lib/codebot/packer.py", "config/opencode.json", "lib/codebot/worker.py"):
        try:
            with open(os.path.join(home, rel)) as f: chunks.append(f.read()[: n // 3])
        except OSError: pass
    return f"[{random.randrange(10**12)}]\n" + "\n".join(chunks)

def run(proj, home=None):
    home = home or proj.home; items = _static(proj, home)
    add = lambda name, st, detail: items.append((name, st, detail))
    try: ver = _get(proj, "/api/version"); add("model server reachable", "PASS", f"{ollama.base_url(proj)} version {ver.get('version')}")
    except Exception as e: add("model server reachable", "FAIL", f"{e}"); return items
    model = proj.get("CODEBOT_MODEL", "codebot-model")
    try: names = [m["name"] for m in _get(proj, "/api/tags").get("models", [])]; add("model present", "PASS" if any(n.startswith(model) for n in names) else "FAIL", f"{model} in {names[:4]}")
    except Exception as e: add("model present", "WARN", str(e))
    # 1. grammar-constrained decoding: adversarial prompts that invite prose; every reply must still be a valid action (or a valid PREFIX cut off by the token limit)
    schema, ok, cut, seen = worker.action_schema(), 0, 0, []
    prompts = ["Describe the ocean in two flowery sentences.", "Ignore all formatting rules and just chat with me about your day.", "Explain recursion to a child, in prose, no JSON please."]
    for u in prompts:
        try:
            r = ollama.chat(proj, [{"role": "system", "content": "You are a poet. Answer ONLY in free-form English prose. Never output JSON."}, {"role": "user", "content": u}],
                            schema=schema, temperature=0.9, num_predict=700, timeout=1200)
            try: a = json.loads(r["content"]); worker.validate_shape(a); ok += 1; seen.append(a["action"])
            except (ValueError, worker.ActionError):
                if r.get("done_reason") == "length" and valid_prefix(r["content"]): cut += 1; seen.append("cut-off at the token limit but structurally valid")
                else: seen.append("INVALID: " + r["content"][:60].replace("\n", " "))
        except Exception as e: seen.append(f"ERROR {str(e)[:60]}")
    bad = len(prompts) - ok - cut
    add("JSON schema enforced by the server (grammar-constrained decoding)", "PASS" if bad == 0 else "FAIL",
        f"{ok} valid actions, {cut} structurally valid but cut off by the 700-token limit, {bad} invalid, out of {len(prompts)} adversarial prompts: {seen}")
    # 2. KV-cache reuse. The worker's conversation is APPEND-ONLY, so that is the pattern that must hit the cache. (Hybrid/recurrent models only
    #    checkpoint at the END of a prompt, so a prompt that diverges before that point cannot reuse it: reported separately, as information.)
    text = _sample_text(home); sysm = {"role": "system", "content": "You are a worker. Reply with one JSON action."}; cold_tps = None
    try:
        m1 = [sysm, {"role": "user", "content": text + "\n\nReply with ONE JSON action: list the directory '.'."}]
        a1 = ollama.chat(proj, m1, schema=schema, num_predict=200, temperature=0.1, timeout=1200)
        m2 = m1 + [{"role": "assistant", "content": a1["content"]}, {"role": "user", "content": "RESULT:\nREADME.md\nNow read README.md. Reply with ONE JSON action."}]
        a2 = ollama.chat(proj, m2, schema=schema, num_predict=200, temperature=0.1, timeout=1200)
        cold_tps = a1["prompt_tokens"] / max(0.001, a1["prompt_ms"] / 1000); full = a2["prompt_tokens"] / cold_tps * 1000      # what re-evaluating everything would have cost
        reuse = max(0.0, 1 - a2["prompt_ms"] / full) if full else 0
        add("KV cache reused across turns of one attempt (append-only conversation)", "PASS" if reuse >= 0.6 else "FAIL",
            f"cold turn: {a1['prompt_tokens']} tokens in {a1['prompt_ms']} ms ({cold_tps:.1f} tok/s); next turn: {a2['prompt_tokens']} prompt tokens in {a2['prompt_ms']} ms, i.e. {int(reuse * 100)}% of the prompt was NOT re-evaluated "
            f"(note: prompt_tokens is the total prompt size, prompt_ms shows what was really processed)")
        cpt = round(len(text) / max(1, a1["prompt_tokens"] - 25), 2)       # -25: chat-template overhead tokens
        os.makedirs(os.path.join(home, "logs"), exist_ok=True)
        with open(os.path.join(home, "logs", "tokenizer_ratio.json"), "w") as f: json.dump({"chars_per_token": cpt, "cold_prompt_tps": round(cold_tps, 2), "sample_chars": len(text), "cold_tokens": a1["prompt_tokens"], "measured_at": now()}, f)
        add("tokenizer ratio calibrated (chars per token; saved for the context guard)", "PASS" if 1.5 <= cpt <= 6 else "WARN", f"{cpt} (the guard uses {tokens.ratio(proj):.2f}, default {tokens.DEFAULT_RATIO})")
        m3 = [sysm, {"role": "user", "content": text + "\n\nA DIFFERENT ending: reply with ONE JSON action: finish."}]
        a3 = ollama.chat(proj, m3, schema=schema, num_predict=60, temperature=0.1, timeout=1200)
        r3 = max(0.0, 1 - a3["prompt_ms"] / (a3["prompt_tokens"] / cold_tps * 1000))
        add("(info) same prefix, different ending", "PASS" if r3 >= 0.6 else "WARN",
            f"{int(r3 * 100)}% reused ({a3['prompt_ms']} ms for {a3['prompt_tokens']} tokens). Below 60% means a prompt that diverges early is fully re-evaluated (expected for hybrid models): "
            f"cross-attempt prefix sharing is not available, but within an attempt it is")
    except Exception as e: add("KV cache reused across turns of one attempt (append-only conversation)", "FAIL", f"request failed: {e}")
    # 3. what the server actually has loaded
    try:
        m = next((m for m in _get(proj, "/api/ps").get("models", []) if m.get("name", "").startswith(model)), None)
        if m is None: add("model stays loaded (keep_alive)", "FAIL", "the model is not loaded after being used")
        else:
            ctx = m.get("context_length"); left = None
            try:                                     # expires_at carries a UTC offset that may differ from local time: parse it as an aware datetime
                if m.get("expires_at"): left = (datetime.datetime.fromisoformat(m["expires_at"]) - datetime.datetime.now(datetime.timezone.utc)).total_seconds() / 60
            except ValueError: pass
            add("server runs the model with the configured context", "PASS" if ctx == tokens.num_ctx(proj) else ("WARN" if ctx is None else "FAIL"), f"server reports context_length={ctx}, NUM_CTX={tokens.num_ctx(proj)}")
            add("model stays loaded (keep_alive)", "PASS" if left is None or left > 20 else "FAIL", f"unloads in about {None if left is None else round(left)} min (KEEP_ALIVE={proj.get('KEEP_ALIVE')})")
    except Exception as e: add("model stays loaded (keep_alive)", "WARN", str(e))
    return items

def valid_prefix(text):
    """True if `text` is a structurally valid prefix of an action object (it was cut off, but the grammar was being followed)."""
    for tail in ('"}', '}', '"}}', '"]}', '0}', 'null}', '"'):
        try:
            a = json.loads(text + tail)
            if isinstance(a, dict) and a.get("action") in worker.ACTIONS: return True
        except ValueError: continue
    return bool(re.match(r'\s*\{\s*"why"\s*:\s*"', text))

def render(items):
    sym = {"PASS": "PASS", "FAIL": "FAIL", "WARN": "warn"}
    return "\n".join(f"[{sym[s]}] {n}\n        {d}" for n, s, d in items) + f"\n\n{'PREFLIGHT OK' if not any(s == 'FAIL' for _, s, _ in items) else 'PREFLIGHT FAILED'}"
