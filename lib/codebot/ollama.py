"""Minimal Ollama /api/chat client (stdlib only). Local only: the host is always 127.0.0.1:<port>."""
import json, time, urllib.error, urllib.request
from . import audit, thermal, tokens

class ModelError(Exception):
    pass

def base_url(proj):
    return f"http://127.0.0.1:{proj.get('OLLAMA_PORT', 11500, int)}"

def _streamed(r, stop_if, t0):
    """Read a streamed reply (one JSON object per line) until it is done or stop_if(text so far), checked at each new line, returns a reason.
    Returns Ollama's final object, or for a stopped reply one made from what arrived (one streamed piece is one token)."""
    parts, thought, pieces = [], [], 0
    for line in r:
        if not line.strip(): continue
        o = json.loads(line.decode()); m = o.get("message") or {}
        parts.append(m.get("content") or ""); thought.append(m.get("thinking") or ""); pieces += 1
        if o.get("done"): o["message"] = {"role": "assistant", "content": "".join(parts), "thinking": "".join(thought)}; return o
        if "\n" in parts[-1]:
            text = "".join(parts); why = stop_if(text)
            if why: return {"message": {"role": "assistant", "content": text, "thinking": "".join(thought)}, "eval_count": pieces, "done_reason": why,
                            "eval_duration": int((time.time() - t0) * 1e9)}
    raise ConnectionError("the model server closed a streamed reply before it was done")

def chat(proj, messages, schema=None, model=None, temperature=None, num_predict=None, timeout=900, retries=3, options=None, think=False, role=None, stop_if=None):
    """One chat call. `schema` constrains the output (structured outputs). Thinking is off unless the caller's role enables it
    (roles.py); a thinking call gets THINK_BUDGET extra output tokens, because its reasoning counts against num_predict. role is recorded in the
    audit log so per-role cost can be measured. stop_if(text so far) -> reason or None streams the reply and ends it early when it returns a reason
    (closing the connection stops the generation; the reason becomes done_reason). Returns dict(content, thinking, prompt_tokens, gen_tokens, secs, ...)."""
    from . import roles
    thermal.wait(proj)
    if think and num_predict: num_predict = int(num_predict) + roles.think_budget(proj)
    body = {"model": model or proj.get("CODEBOT_MODEL", "codebot-model"), "messages": messages, "stream": bool(stop_if), "think": think,
            "keep_alive": proj.get("KEEP_ALIVE", "30m"), "options": {"num_ctx": tokens.num_ctx(proj)}}   # explicit on every request: a different value would reload the model
    if schema is not None: body["format"] = schema
    if temperature is not None: body["options"]["temperature"] = float(temperature)
    if num_predict: body["options"]["num_predict"] = int(num_predict)
    if proj.get("MODEL_SEED"): body["options"]["seed"] = proj.get("MODEL_SEED", 0, int)       # reproducibility knob for benchmarks (unset = the server's default sampling)
    if options: body["options"].update(options)            # e.g. num_thread / num_gpu for the thermal sweep
    data = json.dumps(body).encode()
    last = None
    for attempt in range(retries):
        t0 = time.time()
        req = urllib.request.Request(base_url(proj) + "/api/chat", data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r: out = _streamed(r, stop_if, t0) if stop_if else json.loads(r.read().decode())
            msg = out.get("message") or {}
            ms = lambda k: round(out.get(k, 0) / 1e6)                     # Ollama reports nanoseconds
            st = {"prompt_tokens": out.get("prompt_eval_count", 0), "gen_tokens": out.get("eval_count", 0), "prompt_ms": ms("prompt_eval_duration"),
                  "gen_ms": ms("eval_duration"), "load_ms": ms("load_duration"), "total_ms": ms("total_duration")}
            thinking = msg.get("thinking") or ""
            audit.log(proj, "model_request", endpoint=base_url(proj).split("//")[1], model=body["model"], secs=round(time.time() - t0, 1), **st,
                      **({"role": role} if role else {}), **({"think": think, "thinking_chars": len(thinking)} if think else {}))
            return {"content": msg.get("content", ""), "thinking": thinking, "secs": round(time.time() - t0, 1), "done_reason": out.get("done_reason", ""), **st}
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            last = ModelError(f"HTTP {e.code}: {detail}")
            if e.code == 400: break                         # a bad request will not get better by retrying
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
            last = ModelError(f"cannot reach the local model server: {e}")
        except ValueError as e:                             # not JSON: retrying the same request will not help
            last = ModelError(f"unreadable reply from the model server: {e}"); break
        time.sleep(min(2 ** attempt * 2, 20))               # the server may still be loading the model
    raise last
