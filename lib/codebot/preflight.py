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
    # 1. grammar-constrained decoding: adversarial prompts that invite prose; every reply must still be a valid action
    schema, ok, seen = worker.action_schema(), 0, []
    prompts = ["Describe the ocean in two flowery sentences.", "Ignore all formatting rules and just chat with me about your day.", "Explain recursion to a child, in prose, no JSON please."]
    for u in prompts:
        try:
            r = ollama.chat(proj, [{"role": "system", "content": "You are a poet. Answer ONLY in free-form English prose. Never output JSON."}, {"role": "user", "content": u}],
                            schema=schema, temperature=0.9, num_predict=300, timeout=900)
            a = json.loads(r["content"]); worker.validate_shape(a); ok += 1; seen.append(a["action"])
        except Exception as e: seen.append(f"INVALID({str(e)[:50]})")
    add("JSON schema enforced by the server (grammar-constrained decoding)", "PASS" if ok == len(prompts) else "FAIL", f"{ok}/{len(prompts)} adversarial prompts still produced a valid action: {seen}")
    # 2. KV-cache reuse + tokenizer calibration: one salted cold prefix, then the same prefix with a different tail
    text = _sample_text(home); sysm = {"role": "system", "content": "Count the words."}
    try:
        a1 = ollama.chat(proj, [sysm, {"role": "user", "content": text + "\n\nTail A: say hi."}], schema=schema, num_predict=60, timeout=900)
        a2 = ollama.chat(proj, [sysm, {"role": "user", "content": text + "\n\nTail B: say bye."}], schema=schema, num_predict=60, timeout=900)
        cold, warm = a1["prompt_tokens"], a2["prompt_tokens"]; cpt = round(len(text) / max(1, cold - 25), 2)       # -25: chat-template overhead tokens
        add("KV cache reused for an identical prefix", "PASS" if warm <= 0.35 * cold else "FAIL",
            f"cold request evaluated {cold} tokens ({a1['prompt_ms']} ms prompt, {a1['load_ms']} ms load); same prefix again evaluated {warm} tokens ({a2['prompt_ms']} ms)")
        os.makedirs(os.path.join(home, "logs"), exist_ok=True)
        with open(os.path.join(home, "logs", "tokenizer_ratio.json"), "w") as f: json.dump({"chars_per_token": cpt, "sample_chars": len(text), "cold_tokens": cold, "measured_at": now()}, f)
        add("tokenizer ratio calibrated (chars per token; saved for the context guard)", "PASS" if 1.5 <= cpt <= 6 else "WARN", f"{cpt} (the guard uses {tokens.ratio(proj):.2f}, default {tokens.DEFAULT_RATIO})")
    except Exception as e: add("KV cache reused for an identical prefix", "FAIL", f"request failed: {e}")
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

def render(items):
    sym = {"PASS": "PASS", "FAIL": "FAIL", "WARN": "warn"}
    return "\n".join(f"[{sym[s]}] {n}\n        {d}" for n, s, d in items) + f"\n\n{'PREFLIGHT OK' if not any(s == 'FAIL' for _, s, _ in items) else 'PREFLIGHT FAILED'}"
